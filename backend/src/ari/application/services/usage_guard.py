"""Structural usage limits: the exam quota of the offer and an optional abuse breaker.

Nothing here rations a normal learner by cost. The breaker is off unless an operator sets a
ceiling; the quota is the offer itself (exam sessions included per calendar month).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ari.application.ports.usage import UsageLedger
from ari.domain.errors import QuotaExceededError, UsageCeilingError
from ari.domain.models import LearningMode, utc_now


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The calendar month (UTC) containing `now`, as a half-open window."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


class UsageGuard:
    def __init__(
        self,
        ledger: UsageLedger,
        *,
        exam_sessions_per_month: int,
        cost_ceiling_eur: float | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._ledger = ledger
        self._exam_quota = exam_sessions_per_month
        self._ceiling_micro_eur = (
            None if cost_ceiling_eur is None else round(cost_ceiling_eur * 1_000_000)
        )
        self._clock = clock

    def check_session_start(self, learner_id: str, learning_mode: LearningMode | None) -> None:
        """Before a voice session is created or its call starts."""
        self.check_new_activity(learner_id)
        if learning_mode is not LearningMode.EXAM:
            return
        start, end = month_bounds(self._clock())
        used = self._ledger.exam_sessions_started(learner_id, start, end)
        if used >= self._exam_quota:
            raise QuotaExceededError(
                f"Quota d'examens du mois atteint ({used}/{self._exam_quota}). "
                f"Il se renouvelle le {end:%d/%m/%Y}. L'entraînement reste disponible."
            )

    def check_new_activity(self, learner_id: str) -> None:
        """Before any new activity that calls a model; only the optional breaker applies."""
        if self._ceiling_micro_eur is None:
            return
        start, end = month_bounds(self._clock())
        if self._ledger.cost(learner_id, start, end).known_micro_eur >= self._ceiling_micro_eur:
            raise UsageCeilingError(
                "Votre usage de ce mois a atteint la limite de protection du service. "
                f"Il reprend le {end:%d/%m/%Y}, ou contactez-nous pour le débloquer."
            )
