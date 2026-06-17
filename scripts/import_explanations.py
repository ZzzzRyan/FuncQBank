"""Sync selected content fields (default: explanation) from data/extracted JSON
into an existing database — safely, for production use.

Unlike seed.py (which refuses to touch `verified` rows), this is an explicit,
field-level migration: it updates ONLY the whitelisted fields you ask for, on
rows matched by `rel_path`, and never changes question type/stem/options/answer,
review status, auto_flags, or any user/progress data.

Safety model:
  * Dry-run by default — prints what WOULD change and writes nothing.
  * Pass --apply to actually write; the DB file is backed up first
    (data/app.db -> data/app.db.bak-YYYYmmdd-HHMMSS) unless --no-backup.
  * Only ever writes NON-EMPTY JSON values (never clears a field).
  * By default only fills EMPTY DB fields; pass --overwrite to replace
    existing non-empty values too.

Usage:
    uv run scripts/import_explanations.py                      # preview (no writes)
    uv run scripts/import_explanations.py --apply              # fill empty explanations
    uv run scripts/import_explanations.py --apply --overwrite  # also replace existing ones
    uv run scripts/import_explanations.py --fields explanation,note --apply
    uv run scripts/import_explanations.py --only "3.2" --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Question  # noqa: E402

# Only these content fields may ever be synced by this script. Adding anything
# beyond simple text fields here would risk clobbering reviewed structure.
ALLOWED_FIELDS = ("explanation", "note")


def json_value(rec: dict[str, Any], field: str) -> str | None:
    """Read a field from an extracted record as a clean, non-empty string."""
    val = rec.get(field)
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def db_value(q: Question, field: str) -> str | None:
    val = getattr(q, field, None)
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def shorten(text: str | None, width: int = 60) -> str:
    if not text:
        return "（空）"
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def backup_db() -> Path:
    src = settings.db_path
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = src.with_name(f"{src.name}.bak-{stamp}")
    shutil.copy2(src, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sync explanation/note from data/extracted JSON into the DB (field-level, safe)"
    )
    ap.add_argument(
        "--fields",
        default="explanation",
        help="comma-separated fields to sync (allowed: %s)" % ", ".join(ALLOWED_FIELDS),
    )
    ap.add_argument("--only", default=None, help="substring filter on rel_path")
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing non-empty DB values (default: only fill empty)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write to the DB (default: dry-run preview only)",
    )
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="skip backing up the DB file before applying",
    )
    args = ap.parse_args()

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    bad = [f for f in fields if f not in ALLOWED_FIELDS]
    if bad:
        print(f"不允许同步的字段: {bad}；仅允许 {list(ALLOWED_FIELDS)}", file=sys.stderr)
        return 2
    if not fields:
        print("未指定要同步的字段。", file=sys.stderr)
        return 2

    files = sorted(settings.extracted_dir.rglob("*.json"))
    if not files:
        print(f"未找到提取结果（{settings.extracted_dir}）。", file=sys.stderr)
        return 1

    # Index extracted records by rel_path (the stable key shared with the DB).
    json_by_rel: dict[str, dict[str, Any]] = {}
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        rel = rec.get("rel_path") or str(f.relative_to(settings.extracted_dir).with_suffix(".jpg"))
        if args.only and args.only not in rel:
            continue
        json_by_rel[rel] = rec

    init_db()
    planned: list[tuple[str, str, str | None, str | None]] = []  # (rel, field, old, new)
    skipped_same = skipped_no_json = skipped_has_value = 0
    json_only: list[str] = []

    with Session(engine) as session:
        rows = session.exec(select(Question)).all()
        db_rels = {q.rel_path for q in rows}
        json_only = sorted(rel for rel in json_by_rel if rel not in db_rels)

        for q in rows:
            rec = json_by_rel.get(q.rel_path)
            if rec is None:
                skipped_no_json += 1
                continue
            for field in fields:
                new = json_value(rec, field)
                old = db_value(q, field)
                if new is None:
                    # never clear an existing value with an empty JSON field
                    continue
                if old is not None and not args.overwrite:
                    skipped_has_value += 1
                    continue
                if old == new:
                    skipped_same += 1
                    continue
                planned.append((q.rel_path, field, old, new))
                if args.apply:
                    setattr(q, field, new)

        print(f"DB 题目 {len(rows)} 条；JSON 记录 {len(json_by_rel)} 条；同步字段 {fields}")
        print(
            "计划更新 {n} 处　跳过：值相同 {same}，已有值(未加 --overwrite) {has}，JSON 无对应记录 {nojson}".format(
                n=len(planned), same=skipped_same, has=skipped_has_value, nojson=skipped_no_json
            )
        )
        if json_only:
            print(f"注意：{len(json_only)} 条 JSON 在 DB 中无匹配（不会新建题目）：")
            for rel in json_only[:10]:
                print(f"    · {rel}")
            if len(json_only) > 10:
                print(f"    …… 其余 {len(json_only) - 10} 条略")

        for rel, field, old, new in planned[:40]:
            print(f"  [{field}] {rel}\n      旧：{shorten(old)}\n      新：{shorten(new)}")
        if len(planned) > 40:
            print(f"  …… 其余 {len(planned) - 40} 处变更略")

        if not args.apply:
            print("\n这是预览（dry-run），未写入数据库。确认无误后加 --apply 执行。")
            session.rollback()
            return 0

        if not planned:
            print("\n没有需要写入的变更。")
            return 0

        backup_note = "已跳过备份" if args.no_backup else None
        if not args.no_backup:
            if not settings.db_path.exists():
                print(f"\n数据库文件不存在：{settings.db_path}，无法备份。", file=sys.stderr)
                session.rollback()
                return 1
            dst = backup_db()
            backup_note = f"已备份数据库 → {dst}"

        session.commit()
        print(f"\n{backup_note}")
        print(f"已写入 {len(planned)} 处变更。未改动题型/题干/选项/答案/状态及用户进度。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
