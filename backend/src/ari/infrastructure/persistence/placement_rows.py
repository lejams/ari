from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ari.infrastructure.persistence.base import Base

PLACEMENT_JSON = JSON(none_as_null=True)


class PlacementSetRow(Base):
    """A versioned placement test; content immutable, only the status changes."""

    __tablename__ = "placement_sets"
    __table_args__ = (
        UniqueConstraint("id", "version", "content_hash"),
        CheckConstraint(
            "status IN ('draft_unvalidated', 'published', 'withdrawn')",
            name="ck_placement_workflow_status",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String)


class PlacementSourceRow(Base):
    __tablename__ = "placement_sources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PlacementReviewRow(Base):
    __tablename__ = "placement_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["set_id", "set_version", "set_hash"],
            ["placement_sets.id", "placement_sets.version", "placement_sets.content_hash"],
        ),
    )
    sequence: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String, unique=True)
    set_id: Mapped[str] = mapped_column(String)
    set_version: Mapped[str] = mapped_column(String)
    set_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PlacementEventRow(Base):
    __tablename__ = "placement_publication_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["set_id", "set_version"], ["placement_sets.id", "placement_sets.version"]
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    set_id: Mapped[str] = mapped_column(String)
    set_version: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PlacementAttemptRow(Base):
    __tablename__ = "placement_attempts"
    __table_args__ = (
        UniqueConstraint("learner_id", "request_id", name="uq_placement_start_request"),
        ForeignKeyConstraint(
            ["set_id", "set_version", "set_hash"],
            ["placement_sets.id", "placement_sets.version", "placement_sets.content_hash"],
        ),
        CheckConstraint("status IN ('active','completed','abandoned')", name="ck_placement_status"),
        CheckConstraint(
            "phase IN ('mcq','listening','speaking','completed')", name="ck_placement_phase"
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), index=True)
    request_id: Mapped[str] = mapped_column(String)
    set_id: Mapped[str] = mapped_column(String)
    set_version: Mapped[str] = mapped_column(String)
    set_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String)
    phase: Mapped[str] = mapped_column(String)
    state: Mapped[dict[str, Any]] = mapped_column(PLACEMENT_JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(PLACEMENT_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlacementAnswerRow(Base):
    """One learner answer (MCQ choice or speaking transcript with its rating); append-only."""

    __tablename__ = "placement_answers"
    __table_args__ = (
        UniqueConstraint("attempt_id", "event_id", name="uq_placement_answer_event"),
        UniqueConstraint("attempt_id", "item_id", name="uq_placement_answer_item"),
    )
    attempt_id: Mapped[str] = mapped_column(ForeignKey("placement_attempts.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String)
    item_id: Mapped[str] = mapped_column(String)
    phase: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(PLACEMENT_JSON)
