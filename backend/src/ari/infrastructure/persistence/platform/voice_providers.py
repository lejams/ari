"""Forced training voice per learner account (A/B test of the training voice).

Only the back-office CLI writes `learners.tts_provider`; no learner request can set or clear it.
A learner without it is drawn into the A/B test when a training session is created; a learner
with it always gets that voice. A session already created keeps the voice it was pinned with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ari.domain.accounts import normalise_email
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.infrastructure.persistence.platform.account_rows import LearnerAccountRow
from ari.infrastructure.persistence.platform.repository import LearnerRow

TTS_PROVIDERS: Final = ("openai", "elevenlabs")


@dataclass(frozen=True, slots=True)
class VoiceAssignment:
    email: str
    learner_id: str
    tts_provider: str | None


class VoiceProviders:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def force(self, email: str, provider: str) -> VoiceAssignment:
        """Idempotent: this voice for every training session the account creates from now on."""
        if provider not in TTS_PROVIDERS:
            raise InvalidStateError(
                f"Voix inconnue : {provider} (attendu : {', '.join(TTS_PROVIDERS)})"
            )
        return self._set(email, provider)

    def clear(self, email: str) -> VoiceAssignment:
        """Back to the A/B draw; sessions already created keep their voice."""
        return self._set(email, None)

    def list(self) -> tuple[VoiceAssignment, ...]:
        with Session(self._engine) as db:
            rows = db.execute(
                select(LearnerAccountRow.email, LearnerRow.id, LearnerRow.tts_provider)
                .join(LearnerRow, LearnerRow.id == LearnerAccountRow.learner_id)
                .where(LearnerRow.tts_provider.is_not(None))
                .order_by(LearnerAccountRow.email)
            ).all()
        return tuple(VoiceAssignment(email, learner_id, tts) for email, learner_id, tts in rows)

    def _set(self, email: str, provider: str | None) -> VoiceAssignment:
        address = normalise_email(email)
        with Session(self._engine) as db:
            account = db.scalar(select(LearnerAccountRow).where(LearnerAccountRow.email == address))
            if account is None:
                raise NotFoundError(f"Aucun compte apprenant pour {address}")
            if account.learner_id is None:
                # The setting lives on the learner, which the onboarding creates.
                raise InvalidStateError(
                    f"Le compte {address} n'a pas encore de profil : terminer l'onboarding d'abord"
                )
            learner = db.get(LearnerRow, account.learner_id, with_for_update=True)
            if learner is None:
                raise NotFoundError(f"Learner {account.learner_id} was not found")
            learner.tts_provider = provider
            db.commit()
            return VoiceAssignment(address, learner.id, provider)
