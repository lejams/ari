"""Accounts by invitation, passwords hashed with argon2, server-side sessions.

Nothing is signed: sessions and invitations are random tokens whose SHA-256 is stored, so
there is no shared secret to rotate. Lockout after repeated failures, audit of every step.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from ari.content.domain.accounts import Account, AccountContext, Role
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import new_id

MIN_PASSWORD_LENGTH = 12
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)


class AccountStore(Protocol):
    def create(self, account: Account) -> Account: ...

    def get(self, account_id: str) -> Account | None: ...

    def by_email(self, email: str) -> Account | None: ...

    def list(self) -> tuple[Account, ...]: ...

    def password_hash(self, account_id: str) -> str | None: ...

    def set_password_hash(self, account_id: str, password_hash: str) -> None: ...

    def record_login_failure(self, account_id: str, *, lock_until: datetime | None) -> int: ...

    def reset_login_failures(self, account_id: str) -> None: ...

    def set_active(self, account_id: str, active: bool) -> None: ...

    def add_invitation(
        self, token_hash: str, account_id: str, *, expires_at: datetime, created_by: str | None
    ) -> None: ...

    def invitation_account(self, token_hash: str, now: datetime) -> str | None: ...

    def use_invitation(self, token_hash: str, now: datetime) -> None: ...

    def add_session(
        self, token_hash: str, account_id: str, *, now: datetime, expires_at: datetime
    ) -> None: ...

    def session_account(self, token_hash: str, now: datetime) -> str | None: ...

    def revoke_session(self, token_hash: str, now: datetime) -> None: ...

    def revoke_account_sessions(self, account_id: str, now: datetime) -> None: ...

    def audit(
        self, action: str, *, actor: str | None, target: str | None, payload: dict[str, Any]
    ) -> None: ...


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Invitation:
    account: Account
    token: str  # Shown once to the owner, never stored in clear.
    expires_at: datetime


class BackofficeAuth:
    def __init__(
        self,
        store: AccountStore,
        *,
        session_days: int = 7,
        invitation_hours: int = 48,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._hasher = PasswordHasher()
        self._session_ttl = timedelta(days=session_days)
        self._invitation_ttl = timedelta(hours=invitation_hours)
        self._clock = clock

    # ----- accounts and invitations --------------------------------------------------------

    def create_account(
        self, email: str, display_name: str, roles: Iterable[Role], *, actor: str | None
    ) -> Invitation:
        normalised = email.strip().lower()
        if "@" not in normalised or not display_name.strip():
            raise InvalidStateError("Adresse e-mail et nom d'affichage requis")
        role_set = frozenset(roles)
        if not role_set:
            raise InvalidStateError("Au moins un rôle est requis")
        if self._store.by_email(normalised) is not None:
            raise InvalidStateError("Un compte existe déjà pour cette adresse")
        account = self._store.create(
            Account(
                id=new_id(),
                email=normalised,
                display_name=display_name.strip(),
                roles=role_set,
                created_by_account_id=actor,
                created_at=self._clock(),
            )
        )
        self._store.audit(
            "account_created",
            actor=actor,
            target=account.id,
            payload={"roles": sorted(role.value for role in role_set)},
        )
        return self.invite(account.id, actor=actor)

    def invite(self, account_id: str, *, actor: str | None) -> Invitation:
        """A fresh one-time link; also the password reset path."""
        account = self._store.get(account_id)
        if account is None:
            raise NotFoundError("Compte inconnu")
        if not account.active:
            raise InvalidStateError("Compte désactivé")
        token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._invitation_ttl
        self._store.add_invitation(
            _hash(token), account.id, expires_at=expires_at, created_by=actor
        )
        self._store.audit("invitation_issued", actor=actor, target=account.id, payload={})
        return Invitation(account=account, token=token, expires_at=expires_at)

    def invitation_holder(self, token: str) -> Account | None:
        account_id = self._store.invitation_account(_hash(token), self._clock())
        account = self._store.get(account_id) if account_id else None
        return account if account and account.active else None

    def set_password(self, token: str, password: str) -> Account:
        account = self.invitation_holder(token)
        if account is None:
            raise InvalidStateError("Invitation inconnue, expirée ou déjà utilisée")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidStateError(
                f"Le mot de passe doit faire au moins {MIN_PASSWORD_LENGTH} caractères"
            )
        now = self._clock()
        self._store.set_password_hash(account.id, self._hasher.hash(password))
        self._store.use_invitation(_hash(token), now)
        self._store.revoke_account_sessions(account.id, now)  # A reset logs out everywhere.
        self._store.audit("password_set", actor=account.id, target=account.id, payload={})
        return self._store.get(account.id) or account

    def deactivate(self, account_id: str, *, actor: str) -> Account:
        if account_id == actor:
            raise InvalidStateError("Un compte ne se désactive pas lui-même")
        account = self._store.get(account_id)
        if account is None:
            raise NotFoundError("Compte inconnu")
        self._store.set_active(account_id, False)
        self._store.revoke_account_sessions(account_id, self._clock())
        self._store.audit("account_deactivated", actor=actor, target=account_id, payload={})
        return self._store.get(account_id) or account

    def list_accounts(self) -> tuple[Account, ...]:
        return self._store.list()

    # ----- login and sessions ---------------------------------------------------------------

    def login(self, email: str, password: str) -> str:
        """Returns a session token, or raises. Failures never say which part was wrong."""
        now = self._clock()
        account = self._store.by_email(email.strip().lower())
        failure = InvalidStateError("Identifiants invalides")
        if account is None or not account.active:
            self._store.audit("login_failed", actor=None, target=email.strip().lower(), payload={})
            raise failure
        if account.locked_until is not None and account.locked_until > now:
            raise InvalidStateError("Compte temporairement verrouillé ; réessayez plus tard")
        stored = self._store.password_hash(account.id)
        try:
            if stored is None:
                raise VerifyMismatchError
            self._hasher.verify(stored, password)
        except (VerifyMismatchError, VerificationError):
            failures = account.failed_logins + 1
            lock_until = now + LOCKOUT if failures >= MAX_FAILED_LOGINS else None
            self._store.record_login_failure(account.id, lock_until=lock_until)
            self._store.audit(
                "login_failed", actor=None, target=account.id, payload={"failures": failures}
            )
            raise failure from None
        if self._hasher.check_needs_rehash(stored):
            self._store.set_password_hash(account.id, self._hasher.hash(password))
        self._store.reset_login_failures(account.id)
        token = secrets.token_urlsafe(32)
        self._store.add_session(
            _hash(token), account.id, now=now, expires_at=now + self._session_ttl
        )
        self._store.audit("login", actor=account.id, target=account.id, payload={})
        return token

    def resolve(self, token: str | None) -> AccountContext | None:
        if not token or len(token) > 128:
            return None
        account_id = self._store.session_account(_hash(token), self._clock())
        account = self._store.get(account_id) if account_id else None
        if account is None or not account.active:
            return None
        return AccountContext(id=account.id, display_name=account.display_name, roles=account.roles)

    def logout(self, token: str | None) -> None:
        if token:
            self._store.revoke_session(_hash(token), self._clock())
