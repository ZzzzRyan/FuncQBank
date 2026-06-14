"""Registration, login, logout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.auth import login_user, logout_user, user_from_session
from app.config import settings
from app.db import get_session
from app.models import ROLE_ADMIN, ROLE_USER, User
from app.security import hash_password, rate_limited, verify_csrf, verify_password
from app.templating import page

router = APIRouter()


def _safe_next(nxt: str | None) -> str:
    # only allow local redirects
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return "/"


@router.get("/login")
def login_form(request: Request, session: Session = Depends(get_session), next: str = "/"):
    if user_from_session(request, session):
        return RedirectResponse(_safe_next(next), status_code=303)
    return page(request, session, "login.html", next=_safe_next(next), error=None)


@router.post("/login")
def login_submit(
    request: Request,
    session: Session = Depends(get_session),
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    next: str = Form("/"),
):
    if not verify_csrf(request, csrf_token):
        return page(request, session, "login.html", next=_safe_next(next), error="会话已过期，请重试。")
    ip = request.client.host if request.client else "?"
    if rate_limited(f"login:{ip}", limit=15, window_seconds=300):
        return page(request, session, "login.html", next=_safe_next(next), error="尝试过于频繁，请稍后再试。")

    user = session.exec(select(User).where(User.username == username.strip())).first()
    if not user or not user.is_active or not verify_password(user.password_hash, password):
        return page(request, session, "login.html", next=_safe_next(next), error="用户名或密码错误。")
    login_user(request, user)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.get("/register")
def register_form(request: Request, session: Session = Depends(get_session)):
    if not settings.registration_open:
        return page(request, session, "register.html", closed=True, error=None, values={})
    if user_from_session(request, session):
        return RedirectResponse("/", status_code=303)
    return page(request, session, "register.html", closed=False, error=None, values={})


@router.post("/register")
def register_submit(
    request: Request,
    session: Session = Depends(get_session),
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    display_name: str = Form(""),
    csrf_token: str = Form(""),
):
    values = {"username": username, "display_name": display_name}

    def fail(msg: str):
        return page(request, session, "register.html", closed=False, error=msg, values=values)

    if not settings.registration_open:
        return page(request, session, "register.html", closed=True, error=None, values={})
    if not verify_csrf(request, csrf_token):
        return fail("会话已过期，请重试。")
    ip = request.client.host if request.client else "?"
    if rate_limited(f"register:{ip}", limit=10, window_seconds=600):
        return fail("注册过于频繁，请稍后再试。")

    username = username.strip()
    if not (3 <= len(username) <= 32) or not username.replace("_", "").replace("-", "").isalnum():
        return fail("用户名需为 3–32 位字母/数字/下划线/连字符。")
    if len(password) < 6:
        return fail("密码至少 6 位。")
    if password != password2:
        return fail("两次输入的密码不一致。")
    if session.exec(select(User).where(User.username == username)).first():
        return fail("该用户名已被占用。")

    # First-ever user becomes admin (bootstrap); everyone else is a normal user.
    is_first = session.exec(select(User)).first() is None
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=ROLE_ADMIN if is_first else ROLE_USER,
        display_name=(display_name.strip() or username),
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, session: Session = Depends(get_session), csrf_token: str = Form("")):
    if verify_csrf(request, csrf_token):
        logout_user(request)
    return RedirectResponse("/login", status_code=303)
