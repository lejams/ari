from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ari.infrastructure.persistence.base import Base

PRACTICE_JSON = JSON(none_as_null=True)


class PracticeRunRow(Base):
    __tablename__ = "practice_runs"
    __table_args__ = (
        UniqueConstraint("learner_id", "request_id", name="uq_practice_start_request"),
        CheckConstraint("mode IN ('training','exam')", name="ck_practice_mode"),
        CheckConstraint("status IN ('active','paused','completed')", name="ck_practice_status"),
        CheckConstraint(
            "(status = 'completed' AND feedback IS NOT NULL AND ended_at IS NOT NULL) OR "
            "(status != 'completed' AND feedback IS NULL AND ended_at IS NULL)",
            name="ck_practice_feedback_state",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"))
    request_id: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, Any]] = mapped_column(PRACTICE_JSON)
    feedback: Mapped[dict[str, Any] | None] = mapped_column(PRACTICE_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PracticeAnswerRow(Base):
    __tablename__ = "practice_answers"
    __table_args__ = (
        UniqueConstraint("run_id", "event_id", name="uq_practice_answer_event"),
        UniqueConstraint("run_id", "sequence", name="uq_practice_answer_sequence"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("practice_runs.id"), primary_key=True)
    question_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String)
    sequence: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(PRACTICE_JSON)
