"""Cost read model for the back-office: sums of recorded call costs, unknown costs counted apart.

A NULL cost is never summed as zero: every group reports the known total and how many calls have
an unknown cost, so a missing price shows up instead of shrinking the bill.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Date, Engine, case, cast, func, select
from sqlalchemy.orm import Session

from ari.infrastructure.persistence.content.rows import AiRunRow
from ari.infrastructure.persistence.platform.account_rows import LearnerAccountRow
from ari.infrastructure.persistence.platform.repository import ExecutionRow, SessionRow
from ari.infrastructure.persistence.platform.voice_learning import VoiceLearningRow

RECENT_SESSIONS = 20


def _measures(cost: Any) -> tuple[Any, ...]:
    return (
        func.count().label("calls"),
        func.coalesce(func.sum(cost), 0).label("known_micro_eur"),
        func.count().filter(cost.is_(None)).label("unknown_calls"),
    )


def _group(row: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    group: dict[str, Any] = {key: getattr(row, key) for key in keys}
    group.update(
        calls=int(row.calls),
        known_micro_eur=int(row.known_micro_eur),
        unknown_calls=int(row.unknown_calls),
    )
    return group


def _common(db: Session, table: Any, since: datetime) -> dict[str, Any]:
    cost = table.cost_micro_eur
    window = table.created_at >= since
    total = db.execute(select(*_measures(cost)).where(window)).one()
    day = cast(table.created_at, Date).label("day")
    by_day = db.execute(
        select(day, *_measures(cost)).where(window).group_by(day).order_by(day.desc())
    ).all()
    by_operation = db.execute(
        select(table.operation, table.model, *_measures(cost))
        .where(window)
        .group_by(table.operation, table.model)
        .order_by(func.coalesce(func.sum(cost), 0).desc(), table.operation)
    ).all()
    unknown_reasons = db.execute(
        select(func.jsonb_array_elements_text(table.cost_unknown).label("reason"), func.count())
        .where(window, cost.is_(None))
        .group_by("reason")
    ).all()
    return {
        "total": _group(total, ()),
        "by_day": [{**_group(row, ()), "day": row.day.isoformat()} for row in by_day],
        "by_operation": [_group(row, ("operation", "model")) for row in by_operation],
        "unknown_reasons": {reason: int(count) for reason, count in unknown_reasons},
    }


class CostReport:
    def __init__(self, platform_engine: Engine, content_engine: Engine) -> None:
        self.platform_engine = platform_engine
        self.content_engine = content_engine

    def summary(self, since: datetime) -> dict[str, Any]:
        return {
            "since": since.isoformat(),
            "learners": self._platform(since),
            "content": self._content(since),
        }

    def _platform(self, since: datetime) -> dict[str, Any]:
        table = ExecutionRow
        cost = table.cost_micro_eur
        window = table.created_at >= since
        with Session(self.platform_engine) as db:
            result = _common(db, table, since)
            by_learner = db.execute(
                select(table.learner_id, LearnerAccountRow.email, *_measures(cost))
                .outerjoin(LearnerAccountRow, LearnerAccountRow.learner_id == table.learner_id)
                .where(window)
                .group_by(table.learner_id, LearnerAccountRow.email)
                .order_by(func.coalesce(func.sum(cost), 0).desc())
            ).all()
            kind = case(
                (table.attempt_ref.like("placement:%"), "placement"), else_="session"
            ).label("kind")
            by_kind = db.execute(select(kind, *_measures(cost)).where(window).group_by(kind)).all()
            last_call = func.max(table.created_at).label("last_call_at")
            recent = db.execute(
                select(
                    table.session_id,
                    table.learner_id,
                    SessionRow.case_id,
                    VoiceLearningRow.mode.label("learning_mode"),
                    last_call,
                    *_measures(cost),
                )
                .join(SessionRow, SessionRow.id == table.session_id)
                .outerjoin(VoiceLearningRow, VoiceLearningRow.session_id == table.session_id)
                .where(window)
                .group_by(
                    table.session_id, table.learner_id, SessionRow.case_id, VoiceLearningRow.mode
                )
                .order_by(last_call.desc())
                .limit(RECENT_SESSIONS)
            ).all()
        result["by_learner"] = [_group(row, ("learner_id", "email")) for row in by_learner]
        result["by_kind"] = [_group(row, ("kind",)) for row in by_kind]
        result["recent_sessions"] = [
            {
                **_group(row, ("session_id", "learner_id", "case_id", "learning_mode")),
                "last_call_at": row.last_call_at.isoformat(),
            }
            for row in recent
        ]
        return result

    def _content(self, since: datetime) -> dict[str, Any]:
        with Session(self.content_engine) as db:
            return _common(db, AiRunRow, since)
