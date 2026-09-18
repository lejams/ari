"""Browser possession credential for the existing local learner profile.

Not commercial authentication: no email, password, account recovery or inferred identity.
"""

import hashlib
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Engine, ForeignKey, String, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ari.domain.models import utc_now
from ari.infrastructure.persistence.platform.base import Base


class ProfileCredentialRow(Base):
    __tablename__ = "profile_credentials"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProfileCredentials:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utc_now) -> None:
        self.engine = engine
        self.clock = clock

    def issue(self, learner_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with Session(self.engine) as db, db.begin():
            db.add(
                ProfileCredentialRow(
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    learner_id=learner_id,
                    expires_at=self.clock() + timedelta(days=365),
                )
            )
        return token

    def resolve(self, token: str | None) -> str | None:
        if not token or len(token) > 128:
            return None
        with Session(self.engine) as db:
            return db.scalar(
                select(ProfileCredentialRow.learner_id).where(
                    ProfileCredentialRow.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                    ProfileCredentialRow.expires_at > self.clock(),
                )
            )

    def revoke(self, token: str) -> None:
        with Session(self.engine) as db, db.begin():
            db.execute(
                delete(ProfileCredentialRow).where(
                    ProfileCredentialRow.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                )
            )
