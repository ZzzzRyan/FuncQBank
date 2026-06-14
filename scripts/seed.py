"""Seed / refresh the SQLite DB from data/extracted/*.json.

Idempotent: matches questions by rel_path. Rows already marked `verified`
(human-reviewed) are NEVER overwritten, so re-running after extraction fixes
won't clobber manual corrections.

    uv run scripts/seed.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import (  # noqa: E402
    QUESTION_TYPES,
    STATUS_FLAGGED,
    STATUS_VERIFIED,
    TYPE_JUDGE,
    TYPE_SINGLE,
    Chapter,
    Question,
    Section,
)

JUDGE_VALUES = {"正确", "错误"}


def chapter_title(name: str) -> str:
    return name.replace("_", " ", 1)


def section_parts(name: str) -> tuple[str, str]:
    """'2.1_线性空间' -> ('2.1', '线性空间')."""
    if "_" in name:
        code, title = name.split("_", 1)
    else:
        code, title = name, name
    return code, title


def image_index(filename: str) -> int:
    m = re.search(r"(\d+)", filename)
    return int(m.group(1)) if m else 0


def normalize_answer(qtype: str, answer) -> list[str]:
    if answer is None:
        return []
    if isinstance(answer, list):
        text = "".join(str(a) for a in answer)
    else:
        text = str(answer).strip()
    if qtype == TYPE_JUDGE:
        if "正确" in text:
            return ["正确"]
        if "错误" in text:
            return ["错误"]
        return [text] if text else []
    # single / multiple -> letters
    return [c for c in text.upper() if c.isalpha()]


def get_or_create_chapter(session: Session, name: str, order_index: int) -> Chapter:
    ch = session.exec(select(Chapter).where(Chapter.name == name)).first()
    if ch is None:
        ch = Chapter(name=name, title=chapter_title(name), order_index=order_index)
        session.add(ch)
        session.commit()
        session.refresh(ch)
    return ch


def get_or_create_section(session: Session, chapter_id: int, name: str, order_index: int) -> Section:
    sec = session.exec(
        select(Section).where(Section.chapter_id == chapter_id, Section.name == name)
    ).first()
    code, title = section_parts(name)
    if sec is None:
        sec = Section(chapter_id=chapter_id, name=name, code=code, title=title, order_index=order_index)
        session.add(sec)
        session.commit()
        session.refresh(sec)
    return sec


def main() -> int:
    init_db()
    files = sorted(settings.extracted_dir.rglob("*.json"))
    if not files:
        print(f"未找到提取结果（{settings.extracted_dir}）。请先运行 extract.py。", file=sys.stderr)
        return 1

    created = updated = skipped_verified = 0
    with Session(engine) as session:
        for f in files:
            rec = json.loads(f.read_text(encoding="utf-8"))
            rel = rec.get("rel_path") or str(f.relative_to(settings.extracted_dir).with_suffix(".jpg"))
            parts = Path(rel).parts
            if len(parts) < 3:
                print(f"跳过异常路径: {rel}", file=sys.stderr)
                continue
            chapter_name, section_name, filename = parts[0], parts[1], parts[-1]
            code, _ = section_parts(section_name)
            chapter_order = int(code.split(".")[0]) if code.split(".")[0].isdigit() else 0
            section_order = int(code.split(".")[1]) if "." in code and code.split(".")[1].isdigit() else 0

            ch = get_or_create_chapter(session, chapter_name, chapter_order)
            sec = get_or_create_section(session, ch.id, section_name, section_order)

            qtype = rec.get("type")
            failed = qtype not in QUESTION_TYPES
            if failed:
                qtype = TYPE_SINGLE  # placeholder; row is flagged & hidden from practice

            existing = session.exec(select(Question).where(Question.rel_path == rel)).first()
            if existing and existing.status == STATUS_VERIFIED:
                skipped_verified += 1
                continue

            status = rec.get("status") or STATUS_FLAGGED
            stem = rec.get("stem") or ("（提取失败，请在后台重新提取或手动补充）" if failed else "")
            payload = dict(
                section_id=sec.id,
                filename=filename,
                seq_in_section=rec.get("seq"),
                order_index=image_index(filename),
                type=qtype,
                stem=stem,
                options=rec.get("options") or [],
                answer=normalize_answer(qtype, rec.get("answer")),
                answer_raw=rec.get("answer_raw"),
                points=rec.get("points"),
                explanation=rec.get("explanation"),
                note=rec.get("note"),
                status=status if not failed else STATUS_FLAGGED,
                auto_flags=rec.get("auto_flags") or [],
                model=rec.get("model"),
                extraction_raw=rec.get("raw"),
            )

            if existing is None:
                session.add(Question(rel_path=rel, **payload))
                created += 1
            else:
                for k, v in payload.items():
                    setattr(existing, k, v)
                from datetime import datetime

                existing.updated_at = datetime.utcnow()
                session.add(existing)
                updated += 1
        session.commit()

    print(f"完成：新建 {created}，更新 {updated}，保护已校对(verified) {skipped_verified}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
