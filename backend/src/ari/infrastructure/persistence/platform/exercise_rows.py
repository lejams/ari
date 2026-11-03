"""Tables of the language-exercise bank and of the learners' answers to it.

`language_exercises` holds the exercises: the content never changes (the database refuses any
UPDATE of a content column and any DELETE); only the status moves, and only from `active` to
`withdrawn`. `language_exercise_reviews` is append-only: every check an exercise went through
(blind solver, rubric judge, human reading, learner report) is one row. `language_drills` is the
learners' answers, also append-only.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ari.infrastructure.persistence.platform.base import Base


class LanguageExerciseRow(Base):
    __tablename__ = "language_exercises"
    __table_args__ = (
        UniqueConstraint("id", "content_hash"),
        CheckConstraint("level IN ('B1', 'B2', 'C1')", name="ck_language_exercise_level"),
        CheckConstraint(
            "category IN ('gender', 'case', 'verb_form', 'word_order', 'word_choice', 'register')",
            name="ck_language_exercise_category",
        ),
        CheckConstraint(
            "skill IN ('grammar', 'vocabulary', 'register')", name="ck_language_exercise_skill"
        ),
        CheckConstraint(
            "format IN ('cloze', 'correct_sentence', 'register_choice')",
            name="ck_language_exercise_format",
        ),
        CheckConstraint("status IN ('active', 'withdrawn')", name="ck_language_exercise_status"),
        CheckConstraint(
            "(status = 'withdrawn' AND withdrawn_at IS NOT NULL AND withdrawn_reason IS NOT NULL"
            " AND withdrawn_reason <> '')"
            " OR (status = 'active' AND withdrawn_at IS NULL AND withdrawn_reason IS NULL)",
            name="ck_language_exercise_withdrawal",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Hash of the normalised correct sentence: the same sentence is the same exercise.
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    schema_version: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    skill: Mapped[str] = mapped_column(String)
    format: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The generation run that produced it, and what produced it (models, prompts, versions).
    batch_id: Mapped[str] = mapped_column(String)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)
    withdrawn_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LanguageExerciseReviewRow(Base):
    """One check of one exercise; the verdict is stored with the model and the rubric version."""

    __tablename__ = "language_exercise_reviews"
    __table_args__ = (
        CheckConstraint(
            "reviewer_kind IN ('blind_solver', 'rubric_judge', 'human', 'learner_report')",
            name="ck_language_exercise_reviewer_kind",
        ),
        CheckConstraint(
            "decision IN ('approve', 'reject', 'report')", name="ck_language_exercise_decision"
        ),
    )
    sequence: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    exercise_id: Mapped[str] = mapped_column(ForeignKey("language_exercises.id"), index=True)
    reviewer_kind: Mapped[str] = mapped_column(String)
    # The model and the version of the rubric for a machine ("anthropic:claude-sonnet-5-5@
    # exercise-rubric-v1"), the declared name for a person.
    reviewer_name: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LanguageDrillRow(Base):
    """A learner's answer; the category and level are copied so the profile needs no join."""

    __tablename__ = "language_drills"
    __table_args__ = (
        UniqueConstraint("learner_id", "request_id", name="uq_language_drill_request"),
        # The hash must be the one of the exercise answered: the version the learner saw.
        ForeignKeyConstraint(
            ["exercise_id", "exercise_hash"],
            ["language_exercises.id", "language_exercises.content_hash"],
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), index=True)
    request_id: Mapped[str] = mapped_column(String)
    exercise_id: Mapped[str] = mapped_column(String, index=True)
    exercise_hash: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    chosen_index: Mapped[int] = mapped_column(Integer)
    correct: Mapped[bool] = mapped_column(Boolean)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
