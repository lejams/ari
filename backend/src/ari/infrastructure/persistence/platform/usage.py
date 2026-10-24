"""Usage read model over the platform database: exam sessions started and recorded costs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from ari.application.ports.usage import MonthCost
from ari.infrastructure.persistence.platform.repository import (
    ExecutionRow,
    LearnerRow,
    SessionRow,
)
from ari.infrastructure.persistence.platform.voice_learning import VoiceLearningRow


class SqlUsageLedger:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def exam_sessions_started(self, learner_id: str, start: datetime, end: datetime) -> int:
        """Exam sessions whose call began in the window; a session never opened costs nothing.
        Evaluation sessions never count, even after their account lost the mark."""
        with Session(self._engine) as db:
            return int(
                db.scalar(
                    select(func.count())
                    .select_from(SessionRow)
                    .join(VoiceLearningRow, VoiceLearningRow.session_id == SessionRow.id)
                    .where(
                        SessionRow.learner_id == learner_id,
                        SessionRow.evaluation_account.is_(False),
                        VoiceLearningRow.mode == "exam",
                        SessionRow.call_started_at >= start,
                        SessionRow.call_started_at < end,
                    )
                )
                or 0
            )

    def is_evaluation_account(self, learner_id: str) -> bool:
        with Session(self._engine) as db:
            since = db.scalar(
                select(LearnerRow.evaluation_since).where(LearnerRow.id == learner_id)
            )
        return since is not None

    def cost(self, learner_id: str, start: datetime, end: datetime) -> MonthCost:
        cost = ExecutionRow.cost_micro_eur
        with Session(self._engine) as db:
            known, unknown = db.execute(
                select(
                    func.coalesce(func.sum(cost), 0),
                    func.count().filter(cost.is_(None)),
                ).where(
                    ExecutionRow.learner_id == learner_id,
                    ExecutionRow.created_at >= start,
                    ExecutionRow.created_at < end,
                )
            ).one()
        return MonthCost(known_micro_eur=int(known), unknown_calls=int(unknown))
