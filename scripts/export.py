"""Export all questions from the DB back to JSON (backup / version control).

    uv run scripts/export.py [--out data/export]

Writes one JSON per question mirroring the docs/ folder layout, including the
human-verified status, so corrections made in the admin UI are captured.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Question  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Export questions from DB to JSON")
    ap.add_argument("--out", default=str(settings.data_dir / "export"))
    args = ap.parse_args()
    out_dir = Path(args.out)

    init_db()
    n = 0
    with Session(engine) as session:
        for q in session.exec(select(Question)).all():
            rec = {
                "rel_path": q.rel_path,
                "type": q.type,
                "seq": q.seq_in_section,
                "points": q.points,
                "stem": q.stem,
                "options": q.options,
                "answer": q.answer,
                "answer_raw": q.answer_raw,
                "explanation": q.explanation,
                "note": q.note,
                "status": q.status,
                "auto_flags": q.auto_flags,
                "model": q.model,
            }
            op = out_dir / Path(q.rel_path).with_suffix(".json")
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
    print(f"已导出 {n} 道题到 {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
