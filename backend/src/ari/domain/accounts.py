"""Learner accounts: an e-mail address, a password chosen from a link sent to it, a learner."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ari.domain.errors import InvalidStateError
from ari.domain.models import utc_now

MAX_EMAIL_LENGTH = 254
# One plain mailbox, nothing a mail header could read as a list, a comment or a second line.
_ADDRESS = re.compile(
    r"[a-z0-9._%+-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
)


class AccountTokenPurpose(StrEnum):
    """What a one-time link sent by e-mail is for. Both let the holder set a password."""

    ACTIVATION = "activation"
    PASSWORD_RESET = "password_reset"


@dataclass(frozen=True, slots=True)
class LearnerAccount:
    id: str
    email: str  # stored lower-cased
    learner_id: str | None = None  # set once the onboarding has created the learner
    email_verified_at: datetime | None = None
    active: bool = True
    has_password: bool = False
    failed_logins: int = 0
    locked_until: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)

    @property
    def verified(self) -> bool:
        return self.email_verified_at is not None


def normalise_email(email: str) -> str:
    address = email.strip().lower()
    if len(address) > MAX_EMAIL_LENGTH or not _ADDRESS.fullmatch(address):
        raise InvalidStateError("Adresse e-mail invalide")
    return address
