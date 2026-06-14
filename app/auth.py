"""Authentication dependencies and helpers (session-cookie based)."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from app.db import get_session
from app.models import ROLE_ADMIN, User


def login_user(request: Request, user: User) -> None:
    request.session["user_id"] = user.id


def logout_user(request: Request) -> None:
    request.session.pop("user_id", None)


def user_from_session(request: Request, session: Session) -> Optional[User]:
    """Plain (non-dependency) lookup of the logged-in user, for template context."""
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = session.get(User, uid)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    return user_from_session(request, session)


def require_user(user: Optional[User] = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return user
