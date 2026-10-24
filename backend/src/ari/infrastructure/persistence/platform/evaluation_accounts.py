"""Evaluation accounts: learners an administrator marks to measure ARI (`voice-sim` on staging).

Only the back-office CLI writes this mark; no learner request can set or clear it. A marked
learner has no exam quota, and the sessions it starts are kept out of learner metrics, the
weekly programme and the Costs page (`sessions.evaluation_account`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ari.domain.accounts import normalise_email
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import utc_now
from ari.infrastructure.persistence.platform.account_rows import LearnerAccountRow
from ari.infrastructure.persistence.platform.repository import LearnerRow


@dataclass(frozen=True, slots=True)
class EvaluationAccount:
    email: str
    learner_id: str
    evaluation_since: datetime | None


class EvaluationAccounts:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def mark(self, email: str) -> EvaluationAccount:
        """Idempotent: an account already marked keeps its original date."""
        return self._set(email, marked=True)

    def unmark(self, email: str) -> EvaluationAccount:
        """The quota applies again from now on; past evaluation sessions stay excluded."""
        return self._set(email, marked=False)

    def list(self) -> tuple[EvaluationAccount, ...]:
        with Session(self._engine) as db:
            rows = db.execute(
                select(LearnerAccountRow.email, LearnerRow.id, LearnerRow.evaluation_since)
                .join(LearnerRow, LearnerRow.id == LearnerAccountRow.learner_id)
                .where(LearnerRow.evaluation_since.is_not(None))
                .order_by(LearnerAccountRow.email)
            ).all()
        return tuple(
            EvaluationAccount(email, learner_id, since) for email, learner_id, since in rows
        )

    def _set(self, email: str, *, marked: bool) -> EvaluationAccount:
        address = normalise_email(email)
        with Session(self._engine) as db:
            account = db.scalar(select(LearnerAccountRow).where(LearnerAccountRow.email == address))
            if account is None:
                raise NotFoundError(f"Aucun compte apprenant pour {address}")
            if account.learner_id is None:
                # The mark lives on the learner, which the onboarding creates.
                raise InvalidStateError(
                    f"Le compte {address} n'a pas encore de profil : terminer l'onboarding d'abord"
                )
            learner = db.get(LearnerRow, account.learner_id, with_for_update=True)
            if learner is None:
                raise NotFoundError(f"Learner {account.learner_id} was not found")
            if not marked:
                learner.evaluation_since = None
            elif learner.evaluation_since is None:
                learner.evaluation_since = utc_now()
            db.commit()
            return EvaluationAccount(address, learner.id, learner.evaluation_since)
