"""Back-office accounts: who may upload, review as a physician, review language, or own."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ari.domain.models import utc_now


class Role(StrEnum):
    OWNER = "owner"
    PHYSICIAN_REVIEWER = "physician_reviewer"
    LINGUISTIC_REVIEWER = "linguistic_reviewer"


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    email: str  # stored lower-cased
    display_name: str
    roles: frozenset[Role]
    active: bool = True
    has_password: bool = False
    failed_logins: int = 0
    locked_until: datetime | None = None
    created_by_account_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def has(self, *roles: Role) -> bool:
        return any(role in self.roles for role in roles)


@dataclass(frozen=True, slots=True)
class AccountContext:
    """What a request knows about its caller."""

    id: str
    display_name: str
    roles: frozenset[Role]

    def has(self, *roles: Role) -> bool:
        return any(role in self.roles for role in roles)
