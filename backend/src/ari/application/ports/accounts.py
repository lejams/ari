from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from ari.domain.accounts import AccountTokenPurpose, LearnerAccount


class LearnerAccountStore(Protocol):
    def create(self, account: LearnerAccount) -> LearnerAccount: ...

    def get(self, account_id: str) -> LearnerAccount | None: ...

    def by_email(self, email: str) -> LearnerAccount | None: ...

    def password_hash(self, account_id: str) -> str | None: ...

    def set_password_hash(self, account_id: str, password_hash: str) -> None:
        """Also clears the lockout counters."""
        ...

    def reserve_login_attempt(
        self, account_id: str, now: datetime, *, max_failures: int, lockout: timedelta
    ) -> bool:
        """Atomically counts one attempt, locking at `max_failures`; False while locked."""
        ...

    def reset_login_failures(self, account_id: str) -> None: ...

    def link_learner(self, account_id: str, learner_id: str) -> bool:
        """False when the account already has a learner or the learner another account."""
        ...

    def add_token(
        self,
        token_hash: str,
        account_id: str,
        purpose: AccountTokenPurpose,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> None: ...

    def last_token_at(self, account_id: str, purpose: AccountTokenPurpose) -> datetime | None: ...

    def token_account(self, token_hash: str, now: datetime) -> str | None:
        """The account of a valid, unused, unexpired token, without using it."""
        ...

    def complete_password_link(
        self, token_hash: str, password_hash: str, now: datetime
    ) -> str | None:
        """In one transaction: use the token and every other open link of its active account,
        set the password, verify the address, clear the lockout, revoke all sessions. Returns
        the account, or None when the token is not valid."""
        ...

    def add_session(
        self, token_hash: str, account_id: str, *, now: datetime, expires_at: datetime
    ) -> None: ...

    def session_account(self, token_hash: str, now: datetime) -> str | None: ...

    def revoke_session(self, token_hash: str, now: datetime) -> None: ...
