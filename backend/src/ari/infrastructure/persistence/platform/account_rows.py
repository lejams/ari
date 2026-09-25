"""Learner accounts, the one-time links sent to their address, and server-side sessions."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ari.infrastructure.persistence.platform.base import Base


class LearnerAccountRow(Base):
    __tablename__ = "learner_accounts"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_learner_account_email_lower"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    # One learner per account, one account per learner; empty until the onboarding.
    learner_id: Mapped[str | None] = mapped_column(
        ForeignKey("learners.id"), unique=True, nullable=True
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearnerAccountTokenRow(Base):
    """A one-time link to activate the account or reset its password; only its SHA-256."""

    __tablename__ = "learner_account_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('activation', 'password_reset')", name="ck_learner_token_purpose"
        ),
    )
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("learner_accounts.id"), index=True)
    purpose: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LearnerSessionRow(Base):
    __tablename__ = "learner_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("learner_accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
