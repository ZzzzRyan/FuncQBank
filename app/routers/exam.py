"""Exam mode: 25 judge + 25 choice (mixed single/multiple), 2 pts each, 100 pts total.

Questions are randomly drawn from the visible pool. Each exam sitting creates an
ExamAttempt record so the report persists across page reloads.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app.auth import require_user
from app.config import settings
from app.db import get_session
from app.models import (
    STATUS_FLAGGED,
    TYPE_JUDGE,
    TYPE_MULTIPLE,
    TYPE_SINGLE,
    ExamAttempt,
    Question,
    User,
)
from app.render import render_rich
from app.security import verify_csrf
from app.templating import page

router = APIRouter(prefix="/exam")

VISIBLE = Question.status != STATUS_FLAGGED
JUDGE_COUNT = 25
CHOICE_COUNT = 25
PTS_EACH = 2


def question_payload(q: Question) -> dict[str, Any]:
    return {
        "id": q.id,
        "type": q.type,
        "seq": q.seq_in_section,
        "points": PTS_EACH,
        "stem_html": str(render_rich(q.stem)),
        "options": [
            {"label": o.get("label", ""), "text_html": str(render_rich(o.get("text", "")))}
            for o in (q.options or [])
        ],
        "answer": q.answer or [],
        "answer_raw": q.answer_raw,
        "explanation_html": str(render_rich(q.explanation)) if q.explanation else "",
    }


def _draw(session: Session, qtype: str | list[str], n: int) -> list[Question]:
    """Draw *n* random visible questions of the given type(s) (without replacement)."""
    types = [qtype] if isinstance(qtype, str) else qtype
    pool = session.exec(
        select(Question).where(Question.type.in_(types), VISIBLE)
    ).all()
    if len(pool) <= n:
        return list(pool)
    return random.sample(pool, n)


@router.get("")
def exam_home(request: Request, session: Session = Depends(get_session), user: User = Depends(require_user)):
    attempts = session.exec(
        select(ExamAttempt)
        .where(ExamAttempt.user_id == user.id, ExamAttempt.status == "completed")
        .order_by(ExamAttempt.created_at.desc())
        .limit(20)
    ).all()

    # Quick stats
    total_exams = session.exec(
        select(ExamAttempt).where(
            ExamAttempt.user_id == user.id,
            ExamAttempt.status == "completed",
        )
    ).all()
    avg = round(sum(a.score for a in total_exams) / len(total_exams)) if total_exams else None

    return page(
        request, session, "exam_home.html",
        attempts=attempts, total_exams=len(total_exams), avg_score=avg,
    )


@router.post("/start")
def exam_start(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    if not verify_csrf(request, request.headers.get("x-csrf-token")):
        return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)

    judges = _draw(session, TYPE_JUDGE, JUDGE_COUNT)
    choices = _draw(session, [TYPE_SINGLE, TYPE_MULTIPLE], CHOICE_COUNT)

    if len(judges) < JUDGE_COUNT or len(choices) < CHOICE_COUNT:
        return JSONResponse({
            "ok": False,
            "error": f"题库中题目不足（判断 {len(judges)}/{JUDGE_COUNT}，选择 {len(choices)}/{CHOICE_COUNT}），请先补充题目。"
        }, status_code=200)

    # Interleave: judge, choice, judge, choice, ...
    questions = []
    for i in range(JUDGE_COUNT + CHOICE_COUNT):
        questions.append(judges.pop(0) if judges else choices.pop(0))
    questions.extend(judges or [])
    questions.extend(choices or [])

    attempt = ExamAttempt(
        user_id=user.id,
        question_ids=[q.id for q in questions],
        total=len(questions) * PTS_EACH,
        judge_count=JUDGE_COUNT,
        choice_count=CHOICE_COUNT,
        status="in_progress",
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return JSONResponse({"ok": True, "attempt_id": attempt.id})


@router.get("/take/{attempt_id}")
def exam_take(
    attempt_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    attempt = session.get(ExamAttempt, attempt_id)
    if not attempt or attempt.user_id != user.id:
        return RedirectResponse("/exam", status_code=303)
    if attempt.status == "completed":
        return RedirectResponse(f"/exam/report/{attempt.id}", status_code=303)

    qmap = {}
    for q in session.exec(select(Question).where(Question.id.in_(attempt.question_ids))).all():
        qmap[q.id] = q
    questions = [qmap[qid] for qid in attempt.question_ids if qid in qmap]

    import json as _json

    payload = [question_payload(q) for q in questions]
    return page(
        request,
        session,
        "exam_take.html",
        attempt_id=attempt.id,
        questions_json=_json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c"),
        count=len(payload),
        total_pts=attempt.total,
        judge_count=attempt.judge_count,
        choice_count=attempt.choice_count,
    )


@router.post("/submit/{attempt_id}")
async def exam_submit(
    attempt_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    if not verify_csrf(request, request.headers.get("x-csrf-token")):
        return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)

    attempt = session.get(ExamAttempt, attempt_id)
    if not attempt or attempt.user_id != user.id:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if attempt.status == "completed":
        return JSONResponse({"ok": True, "redirect": f"/exam/report/{attempt.id}"})

    body = await request.json()
    submitted = body.get("answers") or []

    qmap = {}
    for q in session.exec(select(Question).where(Question.id.in_(attempt.question_ids))).all():
        qmap[q.id] = q

    score = 0
    answers = []
    for a in submitted:
        qid = a.get("qid")
        user_ans = set(a.get("user", []) or [])
        q = qmap.get(qid)
        if not q:
            continue
        correct_set = set(q.answer or [])
        is_correct = user_ans == correct_set
        pts = PTS_EACH if is_correct else 0
        score += pts
        answers.append({"qid": qid, "user": sorted(user_ans), "correct": is_correct, "pts": pts})

    attempt.answers = answers
    attempt.score = score
    attempt.status = "completed"
    attempt.total = len(answers) * PTS_EACH
    session.add(attempt)

    # Also write each answer to UserQuestionState (so wrong-book etc. works)
    from app.routers.practice import _get_or_create_state  # noqa: E402

    for a in answers:
        qid = a["qid"]
        st = _get_or_create_state(session, user, qid)
        st.seen_count += 1
        st.last_result = "correct" if a["correct"] else "incorrect"
        if a["correct"]:
            if st.wrong and not st.mastered:
                st.correct_count += 1
                if st.correct_count >= 2:
                    st.wrong = False
                    st.mastered = True
        else:
            st.wrong_count += 1
            st.wrong = True
            st.mastered = False
            st.correct_count = 0
        st.updated_at = datetime.utcnow()
        session.add(st)

    session.commit()
    return JSONResponse({"ok": True, "redirect": f"/exam/report/{attempt.id}"})


@router.get("/report/{attempt_id}")
def exam_report(
    attempt_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    attempt = session.get(ExamAttempt, attempt_id)
    if not attempt or attempt.user_id != user.id:
        return RedirectResponse("/exam", status_code=303)
    if attempt.status != "completed":
        return RedirectResponse(f"/exam/take/{attempt.id}", status_code=303)

    # Load question details
    qmap = {}
    for q in session.exec(select(Question).where(Question.id.in_(attempt.question_ids))).all():
        qmap[q.id] = q

    answers = attempt.answers or []
    correct_count = sum(1 for a in answers if a.get("correct"))
    wrong_count = len(answers) - correct_count

    # Per-type breakdown
    judge_total = judge_correct = choice_total = choice_correct = 0
    for a in answers:
        q = qmap.get(a.get("qid"))
        if not q:
            continue
        if q.type == TYPE_JUDGE:
            judge_total += 1
            if a.get("correct"):
                judge_correct += 1
        else:
            choice_total += 1
            if a.get("correct"):
                choice_correct += 1

    # Build detailed answer list for rendering
    details = []
    for a in answers:
        q = qmap.get(a.get("qid"))
        if not q:
            continue
        user_ans = a.get("user", [])
        details.append({
            "id": q.id,
            "type": q.type,
            "stem_html": str(render_rich(q.stem)),
            "options": [
                {"label": o.get("label", ""), "text_html": str(render_rich(o.get("text", "")))}
                for o in (q.options or [])
            ],
            "correct_answer": q.answer or [],
            "user_answer": user_ans,
            "is_correct": a.get("correct", False),
            "pts": a.get("pts", 0),
            "explanation_html": str(render_rich(q.explanation)) if q.explanation else "",
        })

    # Historical stats
    all_attempts = session.exec(
        select(ExamAttempt).where(
            ExamAttempt.user_id == user.id,
            ExamAttempt.status == "completed",
        ).order_by(ExamAttempt.created_at.asc())
    ).all()
    scores = [a.score for a in all_attempts]
    rank = sum(1 for s in scores if s > attempt.score) + 1 if scores else None
    best = max(scores) if scores else None
    avg = round(sum(scores) / len(scores)) if scores else None

    return page(
        request, session, "exam_report.html",
        attempt=attempt,
        score=attempt.score,
        total=attempt.total,
        correct_count=correct_count,
        wrong_count=wrong_count,
        judge_total=judge_total,
        judge_correct=judge_correct,
        choice_total=choice_total,
        choice_correct=choice_correct,
        details=details,
        rank=rank,
        total_exams=len(scores),
        best_score=best,
        avg_score=avg,
    )
