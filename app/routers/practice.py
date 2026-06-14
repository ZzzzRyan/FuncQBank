"""Browsing, practice flow, progress, wrong-question book, search, attempt APIs."""
from __future__ import annotations

import json
import random
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, func, select

from app.auth import require_user
from app.db import get_session
from app.models import (
    STATUS_FLAGGED,
    TYPE_JUDGE,
    Chapter,
    Question,
    Section,
    User,
    UserQuestionState,
)
from app.render import render_rich
from app.security import verify_csrf
from app.templating import page

router = APIRouter()

VISIBLE = Question.status != STATUS_FLAGGED


def question_payload(q: Question) -> dict[str, Any]:
    return {
        "id": q.id,
        "type": q.type,
        "seq": q.seq_in_section,
        "points": q.points,
        "stem_html": str(render_rich(q.stem)),
        "options": [
            {"label": o.get("label", ""), "text_html": str(render_rich(o.get("text", "")))}
            for o in (q.options or [])
        ],
        "answer": q.answer or [],
        "answer_raw": q.answer_raw,
        "explanation_html": str(render_rich(q.explanation)) if q.explanation else "",
        "section_code": q.section.code if q.section else "",
        "section_title": q.section.title if q.section else "",
    }


def state_map(session: Session, user: User, qids: list[int]) -> dict[int, dict]:
    if not qids:
        return {}
    rows = session.exec(
        select(UserQuestionState).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.question_id.in_(qids),
        )
    ).all()
    return {
        r.question_id: {
            "wrong": r.wrong,
            "mastered": r.mastered,
            "last_result": r.last_result,
            "seen": r.seen_count,
        }
        for r in rows
    }


def render_practice(request: Request, session: Session, user: User, title: str, subtitle: str, questions: list[Question]):
    return page(
        request, session, "practice.html",
        scope_title=title, scope_subtitle=subtitle,
        **_payload_ctx(session, user, questions),
    )


def _payload_ctx(session: Session, user: User, questions: list[Question]) -> dict:
    payload = [question_payload(q) for q in questions]
    states = state_map(session, user, [q.id for q in questions])
    return {
        "questions_json": json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c"),
        "states_json": json.dumps(states, ensure_ascii=False).replace("<", "\\u003c"),
        "count": len(questions),
    }


@router.get("/")
def home(request: Request, session: Session = Depends(get_session), user: User = Depends(require_user)):
    chapters = session.exec(select(Chapter).order_by(Chapter.order_index)).all()
    # counts per section (visible questions)
    data = []
    grand_total = grand_done = grand_wrong = 0
    for ch in chapters:
        secs = session.exec(
            select(Section).where(Section.chapter_id == ch.id).order_by(Section.order_index)
        ).all()
        sec_rows = []
        for sec in secs:
            total = session.exec(
                select(func.count(Question.id)).where(Question.section_id == sec.id, VISIBLE)
            ).one()
            done = session.exec(
                select(func.count(func.distinct(UserQuestionState.question_id)))
                .select_from(UserQuestionState)
                .join(Question, Question.id == UserQuestionState.question_id)
                .where(
                    UserQuestionState.user_id == user.id,
                    UserQuestionState.seen_count > 0,
                    Question.section_id == sec.id,
                    VISIBLE,
                )
            ).one()
            wrong = session.exec(
                select(func.count(UserQuestionState.id))
                .select_from(UserQuestionState)
                .join(Question, Question.id == UserQuestionState.question_id)
                .where(
                    UserQuestionState.user_id == user.id,
                    UserQuestionState.wrong == True,  # noqa: E712
                    UserQuestionState.mastered == False,  # noqa: E712
                    Question.section_id == sec.id,
                    VISIBLE,
                )
            ).one()
            sec_rows.append({"sec": sec, "total": total, "done": done, "wrong": wrong})
            grand_total += total
            grand_done += done
            grand_wrong += wrong
        data.append({"chapter": ch, "sections": sec_rows})

    return page(
        request,
        session,
        "home.html",
        chapters_data=data,
        grand_total=grand_total,
        grand_done=grand_done,
        grand_wrong=grand_wrong,
    )


@router.get("/practice/section/{section_id}")
def practice_section(
    section_id: int,
    request: Request,
    shuffle: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    sec = session.get(Section, section_id)
    if not sec:
        return RedirectResponse("/", status_code=303)
    questions = session.exec(
        select(Question).where(Question.section_id == section_id, VISIBLE).order_by(Question.order_index)
    ).all()
    if shuffle:
        questions = list(questions)
        random.shuffle(questions)
    ch = session.get(Chapter, sec.chapter_id)
    return render_practice(
        request, session, user,
        title=f"{sec.code} {sec.title}",
        subtitle=ch.title if ch else "",
        questions=questions,
    )


@router.get("/practice/wrong")
def practice_wrong(request: Request, session: Session = Depends(get_session), user: User = Depends(require_user)):
    questions = session.exec(
        select(Question)
        .join(UserQuestionState, UserQuestionState.question_id == Question.id)
        .join(Section, Section.id == Question.section_id)
        .join(Chapter, Chapter.id == Section.chapter_id)
        .where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.wrong == True,  # noqa: E712
            UserQuestionState.mastered == False,  # noqa: E712
            VISIBLE,
        )
        .order_by(Chapter.order_index, Section.order_index, Question.order_index)
    ).all()
    return render_practice(request, session, user, title="错题本", subtitle="做错且未掌握的题目", questions=questions)


@router.get("/practice/all")
def practice_all(
    request: Request,
    shuffle: int = 1,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    questions = session.exec(
        select(Question)
        .join(Section, Section.id == Question.section_id)
        .join(Chapter, Chapter.id == Section.chapter_id)
        .where(VISIBLE)
        .order_by(Chapter.order_index, Section.order_index, Question.order_index)
    ).all()
    if shuffle:
        questions = list(questions)
        random.shuffle(questions)
    return render_practice(request, session, user, title="随机练习", subtitle="全部题目（乱序）", questions=questions)


@router.get("/search")
def search(
    request: Request,
    q: str = "",
    type: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    q = (q or "").strip()
    type_ok = type in {"single", "multiple", "judge"}
    searched = bool(q or type_ok)

    questions: list[Question] = []
    if searched:
        stmt = (
            select(Question)
            .join(Section, Section.id == Question.section_id)
            .join(Chapter, Chapter.id == Section.chapter_id)
            .where(VISIBLE)
        )
        if q:
            stmt = stmt.where(Question.stem.like(f"%{q}%"))
        if type_ok:
            stmt = stmt.where(Question.type == type)
        stmt = stmt.order_by(Chapter.order_index, Section.order_index, Question.order_index)
        questions = session.exec(stmt).all()

    if searched:
        from app.templating import TYPE_LABELS

        bits = []
        if q:
            bits.append(f"关键词「{q}」")
        if type_ok:
            bits.append(TYPE_LABELS.get(type, ""))
        subtitle = " · ".join(bits) + f" · 共 {len(questions)} 题"
    else:
        subtitle = "按题干关键词搜索，可叠加题型筛选"

    return page(
        request, session, "search.html",
        q=q, type=type, searched=searched, scope_subtitle=subtitle,
        **_payload_ctx(session, user, questions),
    )


# ---------------- APIs (called via fetch from the practice page) ----------------
async def _json_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


def _get_or_create_state(session: Session, user: User, qid: int) -> UserQuestionState:
    st = session.exec(
        select(UserQuestionState).where(
            UserQuestionState.user_id == user.id, UserQuestionState.question_id == qid
        )
    ).first()
    if st is None:
        st = UserQuestionState(user_id=user.id, question_id=qid)
        session.add(st)
    return st


@router.post("/api/attempt")
async def api_attempt(request: Request, session: Session = Depends(get_session), user: User = Depends(require_user)):
    if not verify_csrf(request, request.headers.get("x-csrf-token")):
        return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
    body = await _json_body(request)
    qid = body.get("question_id")
    result = body.get("result")  # correct | incorrect | revealed
    q = session.get(Question, qid) if isinstance(qid, int) else None
    if not q or result not in {"correct", "incorrect", "revealed"}:
        return JSONResponse({"ok": False, "error": "bad request"}, status_code=400)
    from datetime import datetime

    st = _get_or_create_state(session, user, q.id)
    st.seen_count += 1
    st.last_result = result
    if result == "correct":
        st.correct_count += 1
    elif result == "incorrect":
        st.wrong_count += 1
        st.wrong = True
    st.updated_at = datetime.utcnow()
    session.add(st)
    session.commit()
    return {"ok": True, "wrong": st.wrong, "mastered": st.mastered}


@router.post("/api/state")
async def api_state(request: Request, session: Session = Depends(get_session), user: User = Depends(require_user)):
    if not verify_csrf(request, request.headers.get("x-csrf-token")):
        return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)
    body = await _json_body(request)
    qid = body.get("question_id")
    q = session.get(Question, qid) if isinstance(qid, int) else None
    if not q:
        return JSONResponse({"ok": False, "error": "bad request"}, status_code=400)
    from datetime import datetime

    st = _get_or_create_state(session, user, q.id)
    if "mastered" in body:
        st.mastered = bool(body["mastered"])
    if body.get("remove_wrong"):
        st.wrong = False
    st.updated_at = datetime.utcnow()
    session.add(st)
    session.commit()
    return {"ok": True, "wrong": st.wrong, "mastered": st.mastered}
