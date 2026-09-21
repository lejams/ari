from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ari.infrastructure.persistence.platform.base import Base


class LexiconEntryRow(Base):
    __tablename__ = "learner_lexicon"
    __table_args__ = (UniqueConstraint("learner_id", "lemma_key", name="uq_lexicon_learner_lemma"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), index=True)
    lemma_key: Mapped[str] = mapped_column(String)
    lemma: Mapped[str] = mapped_column(String)
    translation: Mapped[str] = mapped_column(String)
    example: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    srs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    first_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    used_session_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LexiconReviewRow(Base):
    __tablename__ = "lexicon_reviews"
    __table_args__ = (UniqueConstraint("entry_id", "event_id", name="uq_lexicon_review_event"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("learner_lexicon.id"), index=True)
    event_id: Mapped[str] = mapped_column(String)
    rating: Mapped[str] = mapped_column(String)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionLexiconReportRow(Base):
    __tablename__ = "session_lexicon_reports"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
