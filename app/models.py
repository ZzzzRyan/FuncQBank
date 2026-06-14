"""SQLModel ORM models for FuncQBank (SQLite).

Question content is seeded from data/extracted/*.json; per-user practice state
(progress, wrong-question book, mastered) lives in UserQuestionState.
"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy import JSON as SA_JSON
from sqlalchemy import Text as SA_Text
from sqlmodel import Field, Relationship, SQLModel

# --- value sets (kept as plain strings for simplicity) ---
TYPE_SINGLE = "single"
TYPE_MULTIPLE = "multiple"
TYPE_JUDGE = "judge"
QUESTION_TYPES = {TYPE_SINGLE, TYPE_MULTIPLE, TYPE_JUDGE}

STATUS_PENDING = "pending"   # extracted, awaiting human review
STATUS_VERIFIED = "verified"  # human-reviewed & confirmed (protected from re-seed overwrite)
STATUS_FLAGGED = "flagged"    # auto-detected problem, needs attention

ROLE_USER = "user"
ROLE_ADMIN = "admin"


def _utcnow() -> datetime:
    return datetime.utcnow()


class Chapter(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)  # e.g. 第二章_空间理论
    title: str                                   # display, e.g. 第二章 空间理论
    order_index: int = 0
    sections: list["Section"] = Relationship(back_populates="chapter")


class Section(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("chapter_id", "name", name="uq_section_chapter_name"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="chapter.id", index=True)
    name: str            # e.g. 2.1_线性空间
    code: str            # e.g. 2.1
    title: str           # e.g. 线性空间
    order_index: int = 0
    chapter: Optional[Chapter] = Relationship(back_populates="sections")
    questions: list["Question"] = Relationship(back_populates="section")


class Question(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    section_id: int = Field(foreign_key="section.id", index=True)
    rel_path: str = Field(index=True, unique=True)  # 第二章.../2.1.../image1.jpg — stable key
    filename: str                                    # image1.jpg
    seq_in_section: Optional[int] = None             # question number shown in the image
    order_index: int = 0                             # numeric image index (sort order)

    type: str = Field(index=True)
    stem: str = Field(sa_column=Column(SA_Text))
    options: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(SA_JSON))
    answer: list[str] = Field(default_factory=list, sa_column=Column(SA_JSON))  # ["B"] / ["A","B"] / ["正确"]
    answer_raw: Optional[str] = None
    points: Optional[float] = None
    explanation: Optional[str] = Field(default=None, sa_column=Column(SA_Text))
    note: Optional[str] = Field(default=None, sa_column=Column(SA_Text))

    status: str = Field(default=STATUS_PENDING, index=True)
    auto_flags: list[str] = Field(default_factory=list, sa_column=Column(SA_JSON))
    model: Optional[str] = None
    extraction_raw: Optional[str] = Field(default=None, sa_column=Column(SA_Text))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    section: Optional[Section] = Relationship(back_populates="questions")


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: str = Field(default=ROLE_USER)
    display_name: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class UserQuestionState(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_uqs_user_question"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    last_result: Optional[str] = None  # correct | incorrect | revealed
    wrong: bool = Field(default=False, index=True)
    mastered: bool = Field(default=False, index=True)
    seen_count: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)
