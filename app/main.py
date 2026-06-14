"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import Session, select

from app.config import BASE_DIR, settings
from app.db import engine, init_db
from app.models import ROLE_ADMIN, User
from app.routers import admin, auth_routes, practice
from app.templating import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Optional: bootstrap an existing username as admin (set ADMIN_USERNAME in .env).
    if settings.admin_username:
        with Session(engine) as s:
            u = s.exec(select(User).where(User.username == settings.admin_username)).first()
            if u and u.role != ROLE_ADMIN:
                u.role = ROLE_ADMIN
                s.add(u)
                s.commit()
    yield


app = FastAPI(title="FuncQBank", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.cookie_secure,
    max_age=60 * 60 * 24 * 14,  # 14 days
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'",
    )
    return resp


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

app.include_router(auth_routes.router)
app.include_router(practice.router)
app.include_router(admin.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        nxt = request.url.path
        return RedirectResponse(url=f"/login?next={nxt}", status_code=303)
    if exc.status_code == 403:
        return templates.TemplateResponse(
            request, "error.html", {"code": 403, "message": "仅管理员可访问此页面。"}, status_code=403
        )
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request, "error.html", {"code": 404, "message": "页面不存在。"}, status_code=404
        )
    raise exc
