"""Extract structured question data from the question screenshots in docs/.

Usage (run inside the uv project):
    uv run scripts/extract.py --limit 3            # small test batch
    uv run scripts/extract.py                       # full run (skips already-extracted)
    uv run scripts/extract.py --force              # re-extract everything
    uv run scripts/extract.py --only "2.1"        # only images whose rel path contains "2.1"
    uv run scripts/extract.py --workers 6

For each docs/<章>/<节>/imageN.jpg it writes data/extracted/<章>/<节>/imageN.json
containing the structured fields, the raw model output, and auto-detected flags.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Allow running as a plain script: make repo root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app.config import settings  # noqa: E402

VALID_TYPES = {"single", "multiple", "judge"}
JUDGE_ANSWERS = {"正确", "错误"}

SYSTEM_PROMPT = (
    "你是一个严谨的试题OCR与结构化提取助手。你会收到一张泛函分析考试题目的手机截图，"
    "图中含有一道题：题干、选项、选项上的正确性标记（绿色✓=正确选项，红色✗=作答错误），"
    "以及底部灰色区域的「正确答案：X」（少数题在答案下方还有「解析」）。"
    "请严格按要求把它结构化为 JSON，并仅输出 JSON。"
)

USER_PROMPT = r"""请把这张图中的题目提取为严格的 JSON 对象，字段如下：

{
  "type": "single | multiple | judge",   // 单选题→single，多选题→multiple，判断题→judge（依据标题如"1.单选题""3.多选题""7.判断题"）
  "seq": <整数或null>,                      // 题号，标题中"N."的数字；无法判断填 null
  "points": <数字或null>,                   // 分值，"（N 分）"中的数字；无法判断填 null
  "stem": "题干文字",                       // 不要包含题号和"（N分）"
  "options": [ {"label": "A", "text": "选项正文"} ],   // 判断题为 []
  "answer": "B | ABCD | 正确 | 错误",
  "answer_raw": "底部「正确答案：」后的完整原文",
  "explanation": "解析文字或null",
  "note": "识别不确定之处的简要说明，否则null"
}

规则：
1. 所有数学内容（变量、上下标、黑板粗体如 ℝ/ℂ、范数 ‖·‖、内积 ⟨·,·⟩、积分、L^p、l^p、x_n 等）必须用 LaTeX 表示并包裹在 $...$ 内（行内公式）。例：ℝ³→$\mathbb{R}^3$，L²→$L^2$，x_n→$x_n$。
2. 保留题干与选项中的强调：原文加粗/着重的词用 Markdown 加粗 **...** 表示（如 **不是**、**不正确**）。
3. options 中 text 只放选项正文，不要把字母 label 写进 text；选项末尾的标点可保留也可省略。
4. answer：单选给单个大写字母（如 "B"）；多选给所有正确字母按 A→D 顺序拼接（如 "ABCD"）；判断给 "正确" 或 "错误"。判定依据底部「正确答案：」文字，并与选项上的 ✓/✗ 标记互相印证。
5. answer_raw：底部「正确答案：」后面的完整原文（如 "ABCD (少选不得分)" 或 "B" 或 "正确"）。
6. explanation：仅当答案下方确有「解析」或额外说明时提取其文字（公式同样用 $...$），否则 null。

只输出 JSON，不要任何解释文字，不要使用 ``` 代码围栏。"""


def encode_image(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def parse_sse_content(text: str) -> str:
    """Reconstruct assistant content from a raw SSE stream body.

    Some OpenAI-compatible gateways occasionally answer a non-streaming request
    with a `text/event-stream` body (``data: {chunk}\\n\\n ... data: [DONE]``).
    The SDK hands that back as a bare string; here we stitch the delta pieces.
    """
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload == "[DONE]" or not payload:
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        ch = choices[0]
        piece = (ch.get("delta") or {}).get("content")
        if piece is None:
            piece = (ch.get("message") or {}).get("content")
        if piece:
            parts.append(piece)
    return "".join(parts)


def _fix_latex_escapes(s: str) -> str:
    """Repair JSON where LaTeX backslashes weren't doubled (e.g. ``\\mathbb`` written
    as a single backslash). Walks the text, keeping valid JSON escapes (``\\\\``,
    ``\\"``, ``\\n`` …) intact and doubling any other lone backslash."""
    valid = set('"\\/bfnrtu')
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt in valid:
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            out.append("\\\\")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _double_stray_backslashes(s: str) -> str:
    """Double every backslash that isn't part of a JSON ``\\\\`` or ``\\"`` escape.

    This rescues LaTeX commands the model wrote with a *single* backslash
    (``\\rightarrow``, ``\\neq``, ``\\frac`` …): without it ``json.loads`` happily
    eats ``\\r``/``\\t``/``\\n``/``\\b``/``\\f`` as control chars, silently turning
    ``$x_n \\rightarrow x$`` into ``$x_n <CR>ightarrow x$``. Unlike
    :func:`_fix_latex_escapes` this deliberately does NOT treat ``r/t/n/b/f/u``
    as valid — that very leniency is what corrupts inline LaTeX. It keeps already
    correct ``\\\\`` pairs intact, so it's safe to run on well-formed input too."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt in '\\"':
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            out.append("\\\\")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_json_loose(text: str, latex_strict: bool = False) -> dict[str, Any]:
    """Parse JSON that may be fenced, prose-wrapped, or contain unescaped LaTeX.

    ``latex_strict`` doubles stray backslashes *before* parsing — use it when the
    payload is mostly inline ``$...$`` LaTeX (e.g. generated explanations) and has
    no legitimate ``\\n``/``\\t`` whitespace escapes to preserve."""
    t = text.strip()
    if t.startswith("```"):
        segs = t.split("```", 2)
        t = segs[1] if len(segs) >= 2 else text
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
        t = t.strip().rstrip("`").strip()
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        t = t[start : end + 1]
    if latex_strict:
        try:
            return json.loads(_double_stray_backslashes(t))
        except json.JSONDecodeError:
            pass  # fall back to the lenient path below
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return json.loads(_fix_latex_escapes(t))


def compute_flags(d: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    qtype = d.get("type")
    if qtype not in VALID_TYPES:
        flags.append(f"未知题型: {qtype!r}")
    stem = (d.get("stem") or "").strip()
    if not stem:
        flags.append("题干为空")
    options = d.get("options") or []
    answer = (d.get("answer") or "").strip()

    if qtype in {"single", "multiple"}:
        if not options:
            flags.append("选择题无选项")
        labels = {str(o.get("label", "")).strip().upper() for o in options}
        ans_letters = [c for c in answer.upper() if c.isalpha()]
        if not ans_letters:
            flags.append("答案为空")
        for c in ans_letters:
            if c not in labels:
                flags.append(f"答案字母 {c} 不在选项 {sorted(labels)} 中")
        if qtype == "single" and len(ans_letters) != 1:
            flags.append(f"单选题答案应为1个字母，实为 {len(ans_letters)} 个")
        for o in options:
            if not str(o.get("text", "")).strip():
                flags.append(f"选项 {o.get('label')} 正文为空")
    elif qtype == "judge":
        if options:
            flags.append("判断题不应有选项")
        if answer not in JUDGE_ANSWERS:
            flags.append(f"判断题答案应为 正确/错误，实为 {answer!r}")
    return flags


def call_model(client: OpenAI, model: str, image_path: Path, max_retries: int = 5) -> str:
    data_url = encode_image(image_path)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": USER_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                max_tokens=2048,
            )
            # Some OpenAI-compatible gateways occasionally return a bare string
            # (or an error body) instead of a ChatCompletion object.
            if isinstance(resp, str):
                content = resp
            else:
                content = resp.choices[0].message.content or ""
            # Gateway sometimes returns a raw SSE stream body as a string.
            if "data:" in content and "chat.completion" in content:
                content = parse_sse_content(content)
            if not content.strip():
                # Empty completion (gateway/model hiccup) — retry.
                raise RuntimeError("空响应（completion 为空）")
            return content
        except Exception as e:  # noqa: BLE001 — surface as flagged failure after retries
            last_err = e
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"模型调用失败: {last_err}")


def out_path_for(rel: Path) -> Path:
    return settings.extracted_dir / rel.with_suffix(".json")


def extract_one(client: OpenAI, model: str, image_path: Path) -> dict[str, Any]:
    rel = image_path.relative_to(settings.docs_dir)
    record: dict[str, Any] = {
        "rel_path": str(rel),
        "image_path": str(image_path.relative_to(settings.docs_dir.parent)),
        "model": model,
        "status": "pending",
        "auto_flags": [],
    }
    try:
        raw = call_model(client, model, image_path)
        record["raw"] = raw
        parsed = parse_json_loose(raw)
    except Exception as e:  # noqa: BLE001
        record["status"] = "flagged"
        record["auto_flags"] = [f"提取失败: {e}"]
        record.setdefault("raw", "")
        return record

    for key in ("type", "seq", "points", "stem", "options", "answer", "answer_raw", "explanation", "note"):
        record[key] = parsed.get(key)
    record["options"] = record.get("options") or []
    flags = compute_flags(record)
    record["auto_flags"] = flags
    record["status"] = "flagged" if flags else "pending"
    return record


def gather_targets(only: str | None) -> list[Path]:
    targets = sorted(settings.docs_dir.rglob("*.jpg"))
    if only:
        targets = [p for p in targets if only in str(p.relative_to(settings.docs_dir))]
    return targets


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract structured questions from docs/ images via vision LLM")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N targets (0 = all)")
    ap.add_argument("--only", type=str, default=None, help="substring filter on relative image path")
    ap.add_argument("--force", action="store_true", help="re-extract even if output JSON exists")
    ap.add_argument("--retry-flagged", action="store_true", help="re-process existing outputs whose status is 'flagged'")
    ap.add_argument("--workers", type=int, default=6, help="concurrent workers")
    args = ap.parse_args()

    if not settings.openai_apikey or not settings.openai_endpoint or not settings.openai_model:
        print("缺少 OPENAI_ENDPOINT / OPENAI_MODEL / OPENAI_APIKEY（检查 .env）", file=sys.stderr)
        return 2

    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI(base_url=settings.openai_endpoint, api_key=settings.openai_apikey)
    model = settings.openai_model

    targets = gather_targets(args.only)
    def should_process(p: Path) -> bool:
        op = out_path_for(p.relative_to(settings.docs_dir))
        if args.force or not op.exists():
            return True
        if args.retry_flagged:
            try:
                return json.loads(op.read_text(encoding="utf-8")).get("status") == "flagged"
            except Exception:  # noqa: BLE001
                return True
        return False

    targets = [p for p in targets if should_process(p)]
    if args.limit:
        targets = targets[: args.limit]

    total = len(targets)
    print(f"模型={model} 端点={settings.openai_endpoint}")
    print(f"待处理 {total} 张图片（workers={args.workers}, force={args.force}, retry_flagged={args.retry_flagged}）")
    if not total:
        print("没有需要处理的图片。")
        return 0

    done = 0
    flagged = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(extract_one, client, model, p): p for p in targets}
        for fut in as_completed(futures):
            p = futures[fut]
            rel = p.relative_to(settings.docs_dir)
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001
                rec = {
                    "rel_path": str(rel),
                    "image_path": str(p.relative_to(settings.docs_dir.parent)),
                    "model": model,
                    "status": "flagged",
                    "auto_flags": [f"未捕获异常: {e}"],
                    "raw": "",
                }
            op = out_path_for(rel)
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            done += 1
            if rec.get("status") == "flagged":
                flagged += 1
            tag = "FLAG" if rec.get("status") == "flagged" else " ok "
            print(f"[{done}/{total}] {tag} {rel}  {('· ' + '; '.join(rec.get('auto_flags', []))) if rec.get('auto_flags') else ''}")

    print(f"\n完成：{done} 张，其中 {flagged} 张被标记需复核（flagged）。输出目录：{settings.extracted_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
