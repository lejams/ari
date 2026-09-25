"""SQL store for learner accounts, their one-time links and sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, case, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.domain.accounts import AccountTokenPurpose, LearnerAccount
from ari.domain.errors import NotFoundError
from ari.infrastructure.persistence.platform.account_rows import (
    LearnerAccountRow,
    LearnerAccountTokenRow,
    LearnerSessionRow,
)

_TOUCH_INTERVAL = timedelta(minutes=1)  # Sparse `last_seen_at` writes: one per minute at most.


def _dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _account(row: LearnerAccountRow) -> LearnerAccount:
    return LearnerAccount(
        id=row.id,
        email=row.email,
        learner_id=row.learner_id,
        email_verified_at=_dt(row.email_verified_at),
        active=row.active,
        has_password=row.password_hash is not None,
        failed_logins=row.failed_logins,
        locked_until=_dt(row.locked_until),
        created_at=_dt(row.created_at) or datetime.now(UTC),
    )


class SqlLearnerAccountStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ----- accounts ------------------------------------------------------------------

    def create(self, account: LearnerAccount) -> LearnerAccount:
        with Session(self.engine) as db, db.begin():
            db.add(
                LearnerAccountRow(
                    id=account.id,
                    email=account.email,
                    password_hash=None,
                    learner_id=account.learner_id,
                    email_verified_at=account.email_verified_at,
                    active=account.active,
                    failed_logins=0,
                    locked_until=None,
                    created_at=account.created_at,
                )
            )
        return account

    def get(self, account_id: str) -> LearnerAccount | None:
        with Session(self.engine) as db:
            row = db.get(LearnerAccountRow, account_id)
            return _account(row) if row else None

    def by_email(self, email: str) -> LearnerAccount | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(LearnerAccountRow).where(LearnerAccountRow.email == email.lower())
            )
            return _account(row) if row else None

    def password_hash(self, account_id: str) -> str | None:
        with Session(self.engine) as db:
            row = db.get(LearnerAccountRow, account_id)
            return row.password_hash if row else None

    def set_password_hash(self, account_id: str, password_hash: str) -> None:
        with Session(self.engine) as db, db.begin():
            row = self._row(db, account_id)
            row.password_hash = password_hash
            row.failed_logins = 0
            row.locked_until = None

    def reserve_login_attempt(
        self, account_id: str, now: datetime, *, max_failures: int, lockout: timedelta
    ) -> bool:
        # Counted in the database before the password is checked, so parallel guesses cannot
        # all pass the lock test first. A success resets the counter afterwards.
        attempts = LearnerAccountRow.failed_logins + 1
        with Session(self.engine) as db, db.begin():
            reserved = db.scalar(
                update(LearnerAccountRow)
                .where(
                    LearnerAccountRow.id == account_id,
                    or_(
                        LearnerAccountRow.locked_until.is_(None),
                        LearnerAccountRow.locked_until <= now,
                    ),
                )
                .values(
                    failed_logins=attempts,
                    locked_until=case((attempts >= max_failures, now + lockout), else_=None),
                )
                .returning(LearnerAccountRow.id)
            )
            return reserved is not None

    def reset_login_failures(self, account_id: str) -> None:
        with Session(self.engine) as db, db.begin():
            row = self._row(db, account_id)
            row.failed_logins = 0
            row.locked_until = None

    def link_learner(self, account_id: str, learner_id: str) -> bool:
        taken = exists().where(LearnerAccountRow.learner_id == learner_id)
        try:
            with Session(self.engine) as db, db.begin():
                linked = db.scalar(
                    update(LearnerAccountRow)
                    .where(
                        LearnerAccountRow.id == account_id,
                        LearnerAccountRow.learner_id.is_(None),
                        ~taken,
                    )
                    .values(learner_id=learner_id)
                    .returning(LearnerAccountRow.id)
                )
                return linked is not None
        except IntegrityError:  # A concurrent link won the unique constraint.
            return False

    @staticmethod
    def _row(db: Session, account_id: str) -> LearnerAccountRow:
        row = db.get(LearnerAccountRow, account_id)
        if row is None:
            raise NotFoundError("Compte inconnu")
        return row

    # ----- one-time links --------------------------------------------------------------

    def add_token(
        self,
        token_hash: str,
        account_id: str,
        purpose: AccountTokenPurpose,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        with Session(self.engine) as db, db.begin():
            db.add(
                LearnerAccountTokenRow(
                    token_hash=token_hash,
                    account_id=account_id,
                    purpose=purpose.value,
                    created_at=now,
                    expires_at=expires_at,
                )
            )

    def last_token_at(self, account_id: str, purpose: AccountTokenPurpose) -> datetime | None:
        with Session(self.engine) as db:
            return _dt(
                db.scalar(
                    select(func.max(LearnerAccountTokenRow.created_at)).where(
                        LearnerAccountTokenRow.account_id == account_id,
                        LearnerAccountTokenRow.purpose == purpose.value,
                    )
                )
            )

    def token_account(self, token_hash: str, now: datetime) -> str | None:
        with Session(self.engine) as db:
            row = db.get(LearnerAccountTokenRow, token_hash)
            if row is None or row.used_at is not None or (_dt(row.expires_at) or now) <= now:
                return None
            return row.account_id

    def complete_password_link(
        self, token_hash: str, password_hash: str, now: datetime
    ) -> str | None:
        open_link = (
            LearnerAccountTokenRow.used_at.is_(None),
            LearnerAccountTokenRow.expires_at > now,
        )
        with Session(self.engine) as db, db.begin():
            account_id = db.scalar(
                update(LearnerAccountTokenRow)
                .where(LearnerAccountTokenRow.token_hash == token_hash, *open_link)
                .values(used_at=now)
                .returning(LearnerAccountTokenRow.account_id)
            )
            row = (
                db.get(LearnerAccountRow, account_id, with_for_update=True) if account_id else None
            )
            if row is None or not row.active:
                return None
            # Any other link still in a mailbox dies with this one.
            db.execute(
                update(LearnerAccountTokenRow)
                .where(LearnerAccountTokenRow.account_id == row.id, *open_link)
                .values(used_at=now)
            )
            row.password_hash = password_hash
            row.failed_logins = 0
            row.locked_until = None
            if row.email_verified_at is None:
                row.email_verified_at = now
            db.execute(
                update(LearnerSessionRow)
                .where(
                    LearnerSessionRow.account_id == row.id,
                    LearnerSessionRow.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            return row.id

    # ----- sessions ---------------------------------------------------------------------

    def add_session(
        self, token_hash: str, account_id: str, *, now: datetime, expires_at: datetime
    ) -> None:
        with Session(self.engine) as db, db.begin():
            db.add(
                LearnerSessionRow(
                    token_hash=token_hash,
                    account_id=account_id,
                    created_at=now,
                    expires_at=expires_at,
                    last_seen_at=now,
                )
            )

    def session_account(self, token_hash: str, now: datetime) -> str | None:
        with Session(self.engine) as db, db.begin():
            row = db.get(LearnerSessionRow, token_hash)
            if row is None or row.revoked_at is not None or (_dt(row.expires_at) or now) <= now:
                return None
            if now - (_dt(row.last_seen_at) or now) > _TOUCH_INTERVAL:
                row.last_seen_at = now  # Sparse writes: one per minute at most per session.
            return row.account_id

    def revoke_session(self, token_hash: str, now: datetime) -> None:
        with Session(self.engine) as db, db.begin():
            db.execute(
                update(LearnerSessionRow)
                .where(
                    LearnerSessionRow.token_hash == token_hash,
                    LearnerSessionRow.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
