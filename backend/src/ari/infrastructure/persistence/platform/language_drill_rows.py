from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ari.infrastructure.persistence.platform.base import Base


class LanguageDrillRow(Base):
    """One remediation drill: items frozen at creation, answers appended under a row lock."""

    __tablename__ = "language_drill_runs"
    __table_args__ = (
        UniqueConstraint("learner_id", "request_id", name="uq_language_drill_start_request"),
        CheckConstraint("status IN ('active','completed')", name="ck_language_drill_status"),
        CheckConstraint(
            "(status = 'completed') = (ended_at IS NOT NULL)", name="ck_language_drill_ended"
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), index=True)
    request_id: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String)
    focus_category: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    answers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
