"""Create a new admin user, or promote/reset an existing one.

    uv run scripts/create_admin.py <username> [--password PW] [--name "显示名"]

If --password is omitted you'll be prompted. Re-running for an existing user
promotes them to admin and (if a password is given) resets it.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import ROLE_ADMIN, User  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Create or promote an admin user")
    ap.add_argument("username")
    ap.add_argument("--password", default=None)
    ap.add_argument("--name", default=None, help="display name")
    args = ap.parse_args()

    password = args.password
    init_db()
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == args.username)).first()
        if user is None and not password:
            password = getpass.getpass("设置密码: ")
        if user is None:
            user = User(
                username=args.username,
                password_hash=hash_password(password),
                role=ROLE_ADMIN,
                display_name=args.name or args.username,
                is_active=True,
            )
            session.add(user)
            print(f"已创建管理员: {args.username}")
        else:
            user.role = ROLE_ADMIN
            if args.name:
                user.display_name = args.name
            if password:
                user.password_hash = hash_password(password)
            session.add(user)
            print(f"已将 {args.username} 设为管理员" + ("（并重置密码）" if password else ""))
        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
