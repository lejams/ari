"""Cost read model for the back-office: sums of recorded call costs, unknown costs counted apart.

A NULL cost is never summed as zero: every group reports the known total and how many calls have
an unknown cost, so a missing price shows up instead of shrinking the bill.

Calls made for an evaluation account (`voice-sim` on staging) are kept out of every learner group
and of the median, and reported apart as one total, so they neither skew nor vanish.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Date, Engine, case, cast, func, or_, select
from sqlalchemy.orm import Session, aliased

from ari.infrastructure.persistence.content.rows import AiRunRow
from ari.infrastructure.persistence.platform.account_rows import LearnerAccountRow
from ari.infrastructure.persistence.platform.repository import (
    ExecutionRow,
    LearnerRow,
    SessionRow,
)
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


def _evaluation_call() -> ColumnElement[bool]:
    """A call of an evaluation session, or of a learner marked now (placement has no session)."""
    # Aliased so the recent-sessions query, which joins `sessions` itself, keeps its own row.
    session, learner = aliased(SessionRow), aliased(LearnerRow)
    return or_(
        select(session.id)
        .where(session.id == ExecutionRow.session_id, session.evaluation_account)
        .correlate(ExecutionRow)
        .exists(),
        select(learner.id)
        .where(learner.id == ExecutionRow.learner_id, learner.evaluation_since.is_not(None))
        .correlate(ExecutionRow)
        .exists(),
    )


def _common(db: Session, table: Any, window: ColumnElement[bool]) -> dict[str, Any]:
    cost = table.cost_micro_eur
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


def _median(sorted_values: list[int]) -> int:
    if not sorted_values:
        return 0
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[middle]
    return (sorted_values[middle - 1] + sorted_values[middle]) // 2


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
        evaluation = _evaluation_call()
        window = (table.created_at >= since) & ~evaluation
        with Session(self.platform_engine) as db:
            result = _common(db, table, window)
            excluded = db.execute(
                select(*_measures(cost)).where(table.created_at >= since, evaluation)
            ).one()
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
        learners = [_group(row, ("learner_id", "email")) for row in by_learner]
        # Each learner against the median of the active learners (those with a call in the
        # window). A ratio, not a threshold: the owner reads it, nothing is cut on it.
        known = sorted(g["known_micro_eur"] for g in learners if g["learner_id"] is not None)
        median = _median(known)
        for group in learners:
            group["ratio_to_median"] = (
                round(group["known_micro_eur"] / median, 2)
                if median and group["learner_id"] is not None
                else None
            )
        result["median_learner_micro_eur"] = median
        result["evaluation_excluded"] = _group(excluded, ())
        result["by_learner"] = learners
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
            return _common(db, AiRunRow, AiRunRow.created_at >= since)
