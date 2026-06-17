"""Generate short memory-oriented explanations for extracted questions.

Reads data/extracted/**/*.json and writes the generated explanation back to each
JSON record. This is intentionally a separate step between extract.py and seed.py:
extracted JSON remains the content source of truth, and seed.py can then sync the
new explanations into SQLite without changing the schema.

Usage:
    uv run scripts/generate_explanations.py --limit 5
    uv run scripts/generate_explanations.py
    uv run scripts/generate_explanations.py --force          # overwrite existing explanations
    uv run scripts/generate_explanations.py --include-flagged
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app.config import settings  # noqa: E402
from scripts.extract import parse_json_loose, parse_sse_content  # noqa: E402

SYSTEM_PROMPT = (
    "你是泛函分析考试原题题库的助教。你的任务不是写长证明，而是基于已结构化的题干、"
    "选项和标准答案，生成帮助学生记住原题正确选项的简短解析。"
)

USER_PROMPT = """请为下面这道泛函分析原题生成一个简短解析，并严格输出 JSON：

题型：{qtype}
题干：{stem}
选项：
{options}
正确答案：{answer}
原有解析：{old_explanation}

输出格式：
{{"explanation":"..."}}

要求：
1. 解析必须简短，1-3 句即可，偏记忆和辨析，不要写长证明。
2. 如果题干问“错误/不正确/不是/不成立”等，重点说明应选的错误选项为什么错，并给出它的正确说法。
3. 正确选项一般不需要过多解析，更不要简单重复正确选项的内容。
4. 如果有错误选项容易混淆，请用“正确说法是...”点明。
5. 判断题若答案为“错误”，必须给出正确表述或错误点。
6. 若原有解析已经准确，可保留其意思并略作补充；不要编造超出题目信息太远的内容。
7. 数学内容继续使用 $...$ 行内 LaTeX；强调可用 **...**。
8. 只输出 JSON，不要代码围栏，不要额外解释。"""


def answer_text(rec: dict[str, Any]) -> str:
    raw = rec.get("answer_raw")
    if raw:
        return str(raw)
    answer = rec.get("answer")
    if isinstance(answer, list):
        return "".join(str(x) for x in answer)
    return str(answer or "")


def options_text(rec: dict[str, Any]) -> str:
    options = rec.get("options") or []
    if not options:
        return "（无选项）"
    lines = []
    for opt in options:
        label = str(opt.get("label") or "").strip()
        text = str(opt.get("text") or "").strip()
        lines.append(f"{label}. {text}".strip())
    return "\n".join(lines)


def prompt_for(rec: dict[str, Any]) -> str:
    return USER_PROMPT.format(
        qtype=rec.get("type") or "",
        stem=rec.get("stem") or "",
        options=options_text(rec),
        answer=answer_text(rec),
        old_explanation=rec.get("explanation") or "（无）",
    )


def call_model(
    client: OpenAI, model: str, rec: dict[str, Any], max_retries: int = 5
) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt_for(rec)},
    ]
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0.2
            )
            if isinstance(resp, str):
                content = resp
            else:
                content = resp.choices[0].message.content or ""
            if "data:" in content and "chat.completion" in content:
                content = parse_sse_content(content)
            if not content.strip():
                raise RuntimeError("空响应（completion 为空）")
            return content
        except Exception as e:  # noqa: BLE001 — retry gateway/model hiccups
            last_err = e
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"模型调用失败: {last_err}")


def parse_explanation(raw: str) -> str:
    # latex_strict: explanations are mostly inline $...$ LaTeX and never carry
    # real \n/\t whitespace, so single-backslash commands (\rightarrow, \neq …)
    # must be preserved rather than eaten as JSON control chars.
    parsed = parse_json_loose(raw, latex_strict=True)
    explanation = str(parsed.get("explanation") or "").strip()
    if not explanation:
        raise ValueError("模型未返回 explanation")
    return explanation


def generate_one(
    client: OpenAI, model: str, path: Path, dry_run: bool
) -> tuple[Path, str, str | None]:
    rec = json.loads(path.read_text(encoding="utf-8"))
    raw = call_model(client, model, rec)
    explanation = parse_explanation(raw)
    if not dry_run:
        rec["explanation"] = explanation
        rec["explanation_model"] = model
        rec["explanation_raw"] = raw
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, explanation, None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate short explanations for data/extracted JSON records"
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N targets (0 = all)",
    )
    ap.add_argument(
        "--only", type=str, default=None, help="substring filter on extracted JSON path"
    )
    ap.add_argument(
        "--force", action="store_true", help="overwrite existing non-empty explanations"
    )
    ap.add_argument(
        "--include-flagged", action="store_true", help="also process flagged records"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="call the model and print results without writing files",
    )
    ap.add_argument("--workers", type=int, default=4, help="concurrent workers")
    args = ap.parse_args()

    if (
        not settings.openai_apikey
        or not settings.openai_endpoint
        or not settings.openai_model
    ):
        print(
            "缺少 OPENAI_ENDPOINT / OPENAI_MODEL / OPENAI_APIKEY（检查 .env）",
            file=sys.stderr,
        )
        return 2

    files = sorted(settings.extracted_dir.rglob("*.json"))
    targets: list[Path] = []
    skipped_existing = skipped_flagged = skipped_incomplete = 0
    for path in files:
        rec = json.loads(path.read_text(encoding="utf-8"))
        if args.only and args.only not in str(path.relative_to(settings.extracted_dir)):
            continue
        if rec.get("status") == "flagged" and not args.include_flagged:
            skipped_flagged += 1
            continue
        if (rec.get("explanation") or "").strip() and not args.force:
            skipped_existing += 1
            continue
        if not (rec.get("stem") or "").strip() or not answer_text(rec).strip():
            skipped_incomplete += 1
            continue
        targets.append(path)

    if args.limit:
        targets = targets[: args.limit]

    total = len(targets)
    print(f"模型={settings.openai_model} 端点={settings.openai_endpoint}")
    print(
        "待生成 {total} 题（workers={workers}, force={force}, include_flagged={include_flagged}, dry_run={dry_run}）".format(
            total=total,
            workers=args.workers,
            force=args.force,
            include_flagged=args.include_flagged,
            dry_run=args.dry_run,
        )
    )
    print(
        f"跳过：已有解析 {skipped_existing}，flagged {skipped_flagged}，字段不完整 {skipped_incomplete}"
    )
    if not total:
        print("没有需要生成解析的题目。")
        return 0

    client = OpenAI(base_url=settings.openai_endpoint, api_key=settings.openai_apikey)
    done = failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(
                generate_one, client, settings.openai_model, path, args.dry_run
            ): path
            for path in targets
        }
        for fut in as_completed(futures):
            path = futures[fut]
            rel = path.relative_to(settings.extracted_dir)
            try:
                _, explanation, _ = fut.result()
                done += 1
                print(f"[{done + failed}/{total}]  ok  {rel} · {explanation}")
            except Exception as e:  # noqa: BLE001 — report and leave source unchanged
                failed += 1
                print(f"[{done + failed}/{total}] FAIL {rel} · {e}")

    print(
        f"\n完成：成功 {done}，失败 {failed}。失败项未改动；写入后运行 uv run scripts/seed.py 同步数据库。"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
