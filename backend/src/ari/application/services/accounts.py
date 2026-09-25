"""Learner accounts: e-mail address first, password chosen from a link sent to it.

Mirrors the back-office: argon2 hashes, random tokens whose SHA-256 alone is stored, lockout
after repeated failures. The password is only ever set by whoever opens a link sent to the
address, so nobody can pre-register someone else's address with a password they know.
Signup and reset answer the same way whether or not the address has an account: they return
the message to send, and the caller delivers it outside the request.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from ari.application.ports.accounts import LearnerAccountStore
from ari.application.ports.email import EmailMessage
from ari.domain.accounts import AccountTokenPurpose, LearnerAccount, normalise_email
from ari.domain.errors import InvalidStateError
from ari.domain.models import new_id, utc_now

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256  # argon2 hashes the whole input; bound the work per request.
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
EMAIL_COOLDOWN = timedelta(minutes=1)  # At most one link per address and purpose per minute.
# Each argon2 call holds 64 MiB: a burst of logins queues here instead of exhausting memory.
ARGON2_CONCURRENCY = 4


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LearnerAuth:
    def __init__(
        self,
        store: LearnerAccountStore,
        *,
        public_url: str,
        session_days: int = 30,
        activation_hours: int = 48,
        reset_minutes: int = 60,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._public_url = public_url.rstrip("/")
        self._hasher = PasswordHasher()
        self._argon2 = threading.BoundedSemaphore(ARGON2_CONCURRENCY)
        # Addresses without a usable password still pay for one verification, so timing does
        # not tell unknown, never-activated and active accounts apart.
        self._decoy_hash = self._hasher.hash(secrets.token_urlsafe(16))
        self._session_ttl = timedelta(days=session_days)
        self._token_ttl = {
            AccountTokenPurpose.ACTIVATION: timedelta(hours=activation_hours),
            AccountTokenPurpose.PASSWORD_RESET: timedelta(minutes=reset_minutes),
        }
        self._clock = clock

    # ----- links sent by e-mail --------------------------------------------------------------

    def signup(self, email: str) -> EmailMessage | None:
        address = normalise_email(email)
        account = self._store.by_email(address)
        if account is None:
            account = self._store.create(
                LearnerAccount(id=new_id(), email=address, created_at=self._clock())
            )
            logger.info("learner account created %s", account.id)
        if not account.active:
            return None
        if account.verified:
            return self._link(account, AccountTokenPurpose.PASSWORD_RESET, already_registered=True)
        return self._link(account, AccountTokenPurpose.ACTIVATION)

    def request_password_reset(self, email: str) -> EmailMessage | None:
        """Also the way to receive a new activation link."""
        account = self._store.by_email(normalise_email(email))
        if account is None or not account.active:
            return None
        if account.verified:
            return self._link(account, AccountTokenPurpose.PASSWORD_RESET)
        return self._link(account, AccountTokenPurpose.ACTIVATION)

    def link_holder(self, token: str) -> LearnerAccount | None:
        """The account a link would act on, without using it."""
        if not token or len(token) > 128:
            return None
        account_id = self._store.token_account(_hash(token), self._clock())
        account = self._store.get(account_id) if account_id else None
        return account if account and account.active else None

    def set_password(self, token: str, password: str) -> str:
        """Uses the link (and every other open one), verifies the address, logs out
        everywhere; returns a new session token."""
        _check_password(password)
        if not token or len(token) > 128:
            raise InvalidStateError("Lien inconnu, expiré ou déjà utilisé")
        now = self._clock()
        account_id = self._store.complete_password_link(
            _hash(token), self._hash_password(password), now
        )
        if account_id is None:
            raise InvalidStateError("Lien inconnu, expiré ou déjà utilisé")
        logger.info("learner password set %s", account_id)
        return self._open_session(account_id, now)

    def _link(
        self,
        account: LearnerAccount,
        purpose: AccountTokenPurpose,
        *,
        already_registered: bool = False,
    ) -> EmailMessage | None:
        now = self._clock()
        last = self._store.last_token_at(account.id, purpose)
        if last is not None and now - last < EMAIL_COOLDOWN:
            return None
        token = secrets.token_urlsafe(32)
        ttl = self._token_ttl[purpose]
        self._store.add_token(_hash(token), account.id, purpose, now=now, expires_at=now + ttl)
        # The token travels in the fragment: browsers never send it to the server or in Referer.
        link = f"{self._public_url}/connexion#jeton={token}"
        return _message(account.email, purpose, link, ttl, already_registered)

    # ----- login and sessions -----------------------------------------------------------------

    def login(self, email: str, password: str) -> str:
        """Returns a session token, or raises. Failures never say which part was wrong."""
        now = self._clock()
        failure = InvalidStateError("Identifiants invalides")
        account = self._store.by_email(email.strip().lower())
        if account is None or not account.active or len(password) > MAX_PASSWORD_LENGTH:
            self._verify(self._decoy_hash, password[:MAX_PASSWORD_LENGTH])
            raise failure
        if not self._store.reserve_login_attempt(
            account.id, now, max_failures=MAX_FAILED_LOGINS, lockout=LOCKOUT
        ):
            raise InvalidStateError("Compte temporairement verrouillé ; réessayez plus tard")
        stored = self._store.password_hash(account.id)
        matched = self._verify(stored or self._decoy_hash, password)
        if stored is None or not matched:
            logger.info("learner login failed %s", account.id)
            raise failure
        if self._hasher.check_needs_rehash(stored):
            self._store.set_password_hash(account.id, self._hash_password(password))
        self._store.reset_login_failures(account.id)
        return self._open_session(account.id, now)

    def resolve(self, token: str | None) -> LearnerAccount | None:
        if not token or len(token) > 128:
            return None
        account_id = self._store.session_account(_hash(token), self._clock())
        account = self._store.get(account_id) if account_id else None
        if account is None or not account.active or not account.verified:
            return None
        return account

    def logout(self, token: str | None) -> None:
        if token:
            self._store.revoke_session(_hash(token), self._clock())

    def account(self, account_id: str) -> LearnerAccount | None:
        return self._store.get(account_id)

    def link_learner(self, account_id: str, learner_id: str) -> bool:
        return self._store.link_learner(account_id, learner_id)

    def _open_session(self, account_id: str, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        self._store.add_session(
            _hash(token), account_id, now=now, expires_at=now + self._session_ttl
        )
        return token

    def _hash_password(self, password: str) -> str:
        with self._argon2:
            return self._hasher.hash(password)

    def _verify(self, stored: str, password: str) -> bool:
        with self._argon2:
            try:
                return self._hasher.verify(stored, password)
            except (VerifyMismatchError, VerificationError):
                return False


def _check_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise InvalidStateError(
            f"Le mot de passe doit faire entre {MIN_PASSWORD_LENGTH} et "
            f"{MAX_PASSWORD_LENGTH} caractères"
        )


def _duration(ttl: timedelta) -> str:
    hours = int(ttl.total_seconds() // 3600)
    return f"{hours} heures" if hours >= 2 else f"{int(ttl.total_seconds() // 60)} minutes"


def _message(
    to: str, purpose: AccountTokenPurpose, link: str, ttl: timedelta, already_registered: bool
) -> EmailMessage:
    validity = f"Ce lien est valable {_duration(ttl)} et ne sert qu'une fois."
    if purpose is AccountTokenPurpose.ACTIVATION:
        return EmailMessage(
            to=to,
            subject="Activez votre compte ARI",
            text=(
                "Bonjour,\n\n"
                "Pour activer votre compte ARI et choisir votre mot de passe, ouvrez ce lien :\n"
                f"{link}\n\n{validity}\n\n"
                "Si vous n'avez pas demandé de compte, ignorez ce message."
            ),
        )
    opening = (
        "Une inscription a été demandée avec cette adresse, qui a déjà un compte ARI.\n"
        "Pour vous connecter, utilisez votre mot de passe habituel. Pour en choisir un "
        "nouveau, ouvrez ce lien :\n"
        if already_registered
        else "Pour choisir un nouveau mot de passe ARI, ouvrez ce lien :\n"
    )
    return EmailMessage(
        to=to,
        subject="Votre compte ARI" if already_registered else "Nouveau mot de passe ARI",
        text=(
            f"Bonjour,\n\n{opening}{link}\n\n{validity}\n\n"
            "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message : "
            "votre mot de passe actuel reste valable."
        ),
    )
