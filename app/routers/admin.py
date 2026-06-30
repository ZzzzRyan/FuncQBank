"""Admin: review/correct extracted questions, re-extract, manage users.

All routes require an admin user. Original images are served ONLY here (never
under /static and never to normal users), since they contain the answers.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import case
from sqlmodel import Session, func, select

from app.auth import require_admin
from app.config import BASE_DIR, settings
from app.db import get_session
from app.models import (
    QUESTION_TYPES,
    ROLE_ADMIN,
    ROLE_USER,
    STATUS_FLAGGED,
    STATUS_PENDING,
    STATUS_VERIFIED,
    Chapter,
    Question,
    Section,
    User,
)
from app.security import verify_csrf
from app.templating import page

# reuse extraction core for the "re-extract" button
sys.path.insert(0, str(BASE_DIR))
from scripts.extract import call_model, compute_flags, parse_json_loose  # noqa: E402
from scripts.seed import normalize_answer  # noqa: E402

router = APIRouter(prefix="/admin")

PAGE_SIZE = 24


@router.get("")
def admin_list(
    request: Request,
    status: str = "",
    section_id: int = 0,
    page_no: int = 1,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    stmt = select(Question).join(Section, Section.id == Question.section_id).join(
        Chapter, Chapter.id == Section.chapter_id
    )
    if status in {STATUS_PENDING, STATUS_VERIFIED, STATUS_FLAGGED}:
        stmt = stmt.where(Question.status == status)
    if section_id:
        stmt = stmt.where(Question.section_id == section_id)

    # flagged first, then pending, then verified; then natural order
    order_status = case(
        (Question.status == STATUS_FLAGGED, 0),
        (Question.status == STATUS_PENDING, 1),
        else_=2,
    )
    stmt = stmt.order_by(order_status, Chapter.order_index, Section.order_index, Question.order_index)

    total = session.exec(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).one()
    page_no = max(1, page_no)
    rows = session.exec(stmt.offset((page_no - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()

    counts = {
        "all": session.exec(select(func.count(Question.id))).one(),
        "flagged": session.exec(select(func.count(Question.id)).where(Question.status == STATUS_FLAGGED)).one(),
        "pending": session.exec(select(func.count(Question.id)).where(Question.status == STATUS_PENDING)).one(),
        "verified": session.exec(select(func.count(Question.id)).where(Question.status == STATUS_VERIFIED)).one(),
    }
    sections = session.exec(
        select(Section).join(Chapter, Chapter.id == Section.chapter_id).order_by(
            Chapter.order_index, Section.order_index
        )
    ).all()
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return page(
        request, session, "admin/list.html",
        rows=rows, counts=counts, sections=sections,
        cur_status=status, cur_section=section_id,
        page_no=page_no, pages=pages, total=total,
    )


def _next_review_id(session: Session, current: Question) -> int | None:
    """Next flagged-or-pending question to review (for a fast review loop)."""
    nxt = session.exec(
        select(Question.id)
        .join(Section, Section.id == Question.section_id)
        .join(Chapter, Chapter.id == Section.chapter_id)
        .where(Question.status != STATUS_VERIFIED, Question.id != current.id)
        .order_by(
            case((Question.status == STATUS_FLAGGED, 0), else_=1),
            Chapter.order_index, Section.order_index, Question.order_index,
        )
    ).first()
    return nxt


@router.get("/question/{qid}")
def admin_edit(
    qid: int,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    q = session.get(Question, qid)
    if not q:
        return RedirectResponse("/admin", status_code=303)
    image_exists = (settings.docs_dir / q.rel_path).exists()
    return page(
        request, session, "admin/edit.html",
        q=q, sec=q.section, ch=session.get(Chapter, q.section.chapter_id) if q.section else None,
        next_id=_next_review_id(session, q),
        types=sorted(QUESTION_TYPES),
        image_exists=image_exists,
    )


@router.post("/question/{qid}")
async def admin_save(
    qid: int,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    q = session.get(Question, qid)
    if not q:
        return RedirectResponse("/admin", status_code=303)
    form = await request.form()
    if not verify_csrf(request, form.get("csrf_token")):
        return RedirectResponse(f"/admin/question/{qid}", status_code=303)

    qtype = form.get("type", q.type)
    labels = form.getlist("opt_label")
    texts = form.getlist("opt_text")
    options = [
        {"label": (lbl or "").strip(), "text": (txt or "").strip()}
        for lbl, txt in zip(labels, texts)
        if (lbl or "").strip() or (txt or "").strip()
    ]
    if qtype == "judge":
        options = []

    q.type = qtype
    q.stem = form.get("stem", "").strip()
    q.options = options
    q.answer = normalize_answer(qtype, form.get("answer", "").strip())
    q.answer_raw = form.get("answer_raw", "").strip() or None
    pts = form.get("points", "").strip()
    q.points = float(pts) if pts.replace(".", "", 1).isdigit() else None
    q.explanation = form.get("explanation", "").strip() or None
    q.auto_flags = compute_flags(
        {"type": q.type, "stem": q.stem, "options": q.options, "answer": "".join(q.answer)}
    )
    # Saving = human verification (even if auto_flags found something, the human has looked).
    q.status = STATUS_VERIFIED
    q.updated_at = datetime.utcnow()
    session.add(q)
    session.commit()

    action = form.get("action", "save")
    if action == "save_next":
        nxt = _next_review_id(session, q)
        if nxt:
            return RedirectResponse(f"/admin/question/{nxt}", status_code=303)
    return RedirectResponse(f"/admin/question/{qid}", status_code=303)


@router.post("/question/{qid}/reextract")
async def admin_reextract(
    qid: int,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Re-run the vision model and return the fresh result as JSON for the editor
    to load into the form. This does NOT touch the database or data/extracted —
    nothing is saved until the admin reviews and clicks Save. So a failed/empty
    gateway response changes nothing."""
    q = session.get(Question, qid)
    if not q:
        return JSONResponse({"ok": False, "error": "题目不存在"}, status_code=404)
    if not verify_csrf(request, request.headers.get("x-csrf-token")):
        return JSONResponse({"ok": False, "error": "会话已过期，请刷新页面后重试"}, status_code=403)

    from openai import OpenAI

    img = settings.docs_dir / q.rel_path
    if not img.exists():
        return JSONResponse({"ok": False, "error": "原图文件缺失"}, status_code=200)
    try:
        client = OpenAI(base_url=settings.openai_endpoint, api_key=settings.openai_apikey)
        raw = call_model(client, settings.openai_model, img)
        parsed = parse_json_loose(raw)
    except Exception as e:  # noqa: BLE001 — report, change nothing
        return JSONResponse({"ok": False, "error": f"识别失败（{e}），未改动任何数据，可重试。"}, status_code=200)

    qtype = parsed.get("type") if parsed.get("type") in QUESTION_TYPES else q.type
    options = [{"label": o.get("label", ""), "text": o.get("text", "")} for o in (parsed.get("options") or [])]
    answer = "".join(normalize_answer(qtype, parsed.get("answer")))
    fields = {
        "type": qtype,
        "stem": parsed.get("stem") or "",
        "options": options,
        "answer": answer,
        "answer_raw": parsed.get("answer_raw") or "",
        "points": parsed.get("points"),
        "explanation": parsed.get("explanation") or "",
        "note": parsed.get("note") or "",
        "auto_flags": compute_flags({"type": qtype, "stem": parsed.get("stem") or "", "options": options, "answer": answer}),
    }
    return JSONResponse({"ok": True, "fields": fields})


@router.get("/image/{qid}")
def admin_image(qid: int, session: Session = Depends(get_session), admin: User = Depends(require_admin)):
    q = session.get(Question, qid)
    if not q:
        raise HTTPException(status_code=404, detail="题目不存在")
    path = settings.docs_dir / q.rel_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="原图文件缺失")
    return FileResponse(str(path), media_type="image/jpeg")


@router.get("/users")
def admin_users(request: Request, session: Session = Depends(get_session), admin: User = Depends(require_admin)):
    users = session.exec(select(User).order_by(User.created_at)).all()
    return page(request, session, "admin/users.html", users=users)


@router.post("/users/{uid}")
async def admin_user_action(
    uid: int,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    form = await request.form()
    if not verify_csrf(request, form.get("csrf_token")):
        return RedirectResponse("/admin/users", status_code=303)
    u = session.get(User, uid)
    action = form.get("action")
    if u and u.id != admin.id:  # never lock yourself out
        if action == "promote":
            u.role = ROLE_ADMIN
        elif action == "demote":
            u.role = ROLE_USER
        elif action == "toggle_active":
            u.is_active = not u.is_active
        session.add(u)
        session.commit()
    return RedirectResponse("/admin/users", status_code=303)
