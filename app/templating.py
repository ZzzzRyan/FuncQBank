"""Jinja2 templating setup + a `page()` helper that injects common context."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.auth import user_from_session
from app.config import BASE_DIR, settings
from app.render import render_rich
from app.security import get_csrf_token

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
templates.env.filters["rich"] = render_rich
templates.env.globals["registration_open"] = settings.registration_open
templates.env.globals["app_name"] = "泛函题库"
templates.env.globals["beian"] = settings.beian
templates.env.globals["asset_v"] = "13"  # bump to bust browser cache of app.css/app.js

TYPE_LABELS = {"single": "单选题", "multiple": "多选题", "judge": "判断题"}
templates.env.globals["type_labels"] = TYPE_LABELS


def page(request: Request, session: Session, name: str, **ctx: Any):
    context = {
        "user": user_from_session(request, session),
        "csrf_token": get_csrf_token(request),
        "settings": settings,
    }
    context.update(ctx)
    return templates.TemplateResponse(request, name, context)
