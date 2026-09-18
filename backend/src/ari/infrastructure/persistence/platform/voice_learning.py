"""Explicit learning mode; absence of a row means historical mode unknown."""

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ari.infrastructure.persistence.platform.base import Base


class VoiceLearningRow(Base):
    __tablename__ = "voice_learning_context"
    __table_args__ = (
        CheckConstraint("mode IN ('training','exam')", name="ck_voice_learning_mode"),
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String)


class VoiceStartRow(Base):
    __tablename__ = "voice_start_requests"
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), unique=True)
