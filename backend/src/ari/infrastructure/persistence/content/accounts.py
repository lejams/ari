"""SQL store for back-office accounts, invitations, sessions and audit rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from ari.content.domain.accounts import Account, Role
from ari.domain.errors import NotFoundError
from ari.domain.models import new_id
from ari.infrastructure.persistence.content.auth_rows import (
    BackofficeAccountRow,
    BackofficeAuditRow,
    BackofficeInvitationRow,
    BackofficeSessionRow,
)

_TOUCH_INTERVAL = timedelta(minutes=1)  # Sparse `last_seen_at` writes: one per minute at most.


def _dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _account(row: BackofficeAccountRow) -> Account:
    return Account(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        roles=frozenset(Role(role) for role in row.roles),
        active=row.active,
        has_password=row.password_hash is not None,
        failed_logins=row.failed_logins,
        locked_until=_dt(row.locked_until),
        created_by_account_id=row.created_by_account_id,
        created_at=_dt(row.created_at) or datetime.now(UTC),
    )


class SqlAccountStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ----- accounts ------------------------------------------------------------------

    def create(self, account: Account) -> Account:
        with Session(self.engine) as db, db.begin():
            db.add(
                BackofficeAccountRow(
                    id=account.id,
                    email=account.email,
                    display_name=account.display_name,
                    password_hash=None,
                    roles=sorted(role.value for role in account.roles),
                    active=account.active,
                    failed_logins=0,
                    locked_until=None,
                    created_by_account_id=account.created_by_account_id,
                    created_at=account.created_at,
                )
            )
        return account

    def get(self, account_id: str) -> Account | None:
        with Session(self.engine) as db:
            row = db.get(BackofficeAccountRow, account_id)
            return _account(row) if row else None

    def by_email(self, email: str) -> Account | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(BackofficeAccountRow).where(BackofficeAccountRow.email == email.lower())
            )
            return _account(row) if row else None

    def list(self) -> tuple[Account, ...]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(BackofficeAccountRow).order_by(
                    BackofficeAccountRow.created_at, BackofficeAccountRow.id
                )
            )
            return tuple(_account(row) for row in rows)

    def password_hash(self, account_id: str) -> str | None:
        with Session(self.engine) as db:
            row = db.get(BackofficeAccountRow, account_id)
            return row.password_hash if row else None

    def set_password_hash(self, account_id: str, password_hash: str) -> None:
        with Session(self.engine) as db, db.begin():
            row = self._row(db, account_id)
            row.password_hash = password_hash
            row.failed_logins = 0
            row.locked_until = None

    def record_login_failure(self, account_id: str, *, lock_until: datetime | None) -> int:
        with Session(self.engine) as db, db.begin():
            row = self._row(db, account_id)
            row.failed_logins += 1
            if lock_until is not None:
                row.locked_until = lock_until
            return row.failed_logins

    def reset_login_failures(self, account_id: str) -> None:
        with Session(self.engine) as db, db.begin():
            row = self._row(db, account_id)
            row.failed_logins = 0
            row.locked_until = None

    def set_active(self, account_id: str, active: bool) -> None:
        with Session(self.engine) as db, db.begin():
            self._row(db, account_id).active = active

    @staticmethod
    def _row(db: Session, account_id: str) -> BackofficeAccountRow:
        row = db.get(BackofficeAccountRow, account_id)
        if row is None:
            raise NotFoundError("Compte inconnu")
        return row

    # ----- invitations -----------------------------------------------------------------

    def add_invitation(
        self, token_hash: str, account_id: str, *, expires_at: datetime, created_by: str | None
    ) -> None:
        with Session(self.engine) as db, db.begin():
            db.add(
                BackofficeInvitationRow(
                    token_hash=token_hash,
                    account_id=account_id,
                    expires_at=expires_at,
                    created_by_account_id=created_by,
                    created_at=datetime.now(UTC),
                )
            )

    def invitation_account(self, token_hash: str, now: datetime) -> str | None:
        """The account of a valid, unused, unexpired invitation."""
        with Session(self.engine) as db:
            row = db.get(BackofficeInvitationRow, token_hash)
            if row is None or row.used_at is not None or (_dt(row.expires_at) or now) <= now:
                return None
            return row.account_id

    def use_invitation(self, token_hash: str, now: datetime) -> None:
        with Session(self.engine) as db, db.begin():
            db.execute(
                update(BackofficeInvitationRow)
                .where(BackofficeInvitationRow.token_hash == token_hash)
                .values(used_at=now)
            )

    # ----- sessions ---------------------------------------------------------------------

    def add_session(
        self, token_hash: str, account_id: str, *, now: datetime, expires_at: datetime
    ) -> None:
        with Session(self.engine) as db, db.begin():
            db.add(
                BackofficeSessionRow(
                    token_hash=token_hash,
                    account_id=account_id,
                    created_at=now,
                    expires_at=expires_at,
                    last_seen_at=now,
                )
            )

    def session_account(self, token_hash: str, now: datetime) -> str | None:
        with Session(self.engine) as db, db.begin():
            row = db.get(BackofficeSessionRow, token_hash)
            if row is None or row.revoked_at is not None or (_dt(row.expires_at) or now) <= now:
                return None
            if now - (_dt(row.last_seen_at) or now) > _TOUCH_INTERVAL:
                row.last_seen_at = now  # Sparse writes: one per minute at most per session.
            return row.account_id

    def revoke_session(self, token_hash: str, now: datetime) -> None:
        with Session(self.engine) as db, db.begin():
            db.execute(
                update(BackofficeSessionRow)
                .where(
                    BackofficeSessionRow.token_hash == token_hash,
                    BackofficeSessionRow.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )

    def revoke_account_sessions(self, account_id: str, now: datetime) -> None:
        with Session(self.engine) as db, db.begin():
            db.execute(
                update(BackofficeSessionRow)
                .where(
                    BackofficeSessionRow.account_id == account_id,
                    BackofficeSessionRow.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )

    # ----- audit ------------------------------------------------------------------------

    def audit(
        self, action: str, *, actor: str | None, target: str | None, payload: dict[str, Any]
    ) -> None:
        with Session(self.engine) as db, db.begin():
            db.add(
                BackofficeAuditRow(
                    id=new_id(),
                    actor_account_id=actor,
                    action=action,
                    target=target,
                    payload=payload,
                    created_at=datetime.now(UTC),
                )
            )

    def audit_entries(self, *, action: str | None = None) -> tuple[dict[str, Any], ...]:
        statement = select(BackofficeAuditRow).order_by(BackofficeAuditRow.created_at)
        if action:
            statement = statement.where(BackofficeAuditRow.action == action)
        with Session(self.engine) as db:
            return tuple(
                {
                    "id": row.id,
                    "actor_account_id": row.actor_account_id,
                    "action": row.action,
                    "target": row.target,
                    "payload": dict(row.payload),
                    "created_at": (_dt(row.created_at) or datetime.now(UTC)).isoformat(),
                }
                for row in db.scalars(statement)
            )
