"""Placement attempts: owned, idempotent start and answers, append-only answers."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import utc_now
from ari.infrastructure.persistence.platform.placement_rows import (
    PlacementAnswerRow,
    PlacementAttemptRow,
)


@dataclass(frozen=True, slots=True)
class PlacementAttempt:
    id: str
    learner_id: str
    request_id: str
    set_id: str
    set_version: str
    set_hash: str
    status: str  # active | completed | abandoned
    phase: str  # mcq | listening | speaking | completed
    state: dict[str, Any]
    answers: tuple[dict[str, Any], ...] = ()
    result: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None

    def answered(self, item_id: str) -> bool:
        return any(answer["item_id"] == item_id for answer in self.answers)


def _dt(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SqlPlacementRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _read(self, db: Session, row: PlacementAttemptRow) -> PlacementAttempt:
        answers = tuple(
            {
                "sequence": a.sequence,
                "event_id": a.event_id,
                "item_id": a.item_id,
                "phase": a.phase,
                **a.payload,
            }
            for a in db.scalars(
                select(PlacementAnswerRow)
                .where(PlacementAnswerRow.attempt_id == row.id)
                .order_by(PlacementAnswerRow.sequence)
            )
        )
        return PlacementAttempt(
            id=row.id,
            learner_id=row.learner_id,
            request_id=row.request_id,
            set_id=row.set_id,
            set_version=row.set_version,
            set_hash=row.set_hash,
            status=row.status,
            phase=row.phase,
            state=dict(row.state),
            answers=answers,
            result=dict(row.result) if row.result else None,
            created_at=_dt(row.created_at),
            ended_at=_dt(row.ended_at) if row.ended_at else None,
        )

    def _lock(self, db: Session, attempt_id: str, learner_id: str) -> PlacementAttemptRow:
        row = db.scalar(
            select(PlacementAttemptRow)
            .where(PlacementAttemptRow.id == attempt_id)
            .with_for_update()
        )
        if row is None or row.learner_id != learner_id:
            raise NotFoundError("Test de niveau introuvable")
        return row

    def get(self, attempt_id: str, learner_id: str) -> PlacementAttempt:
        with Session(self.engine) as db:
            row = db.get(PlacementAttemptRow, attempt_id)
            if row is None or row.learner_id != learner_id:
                raise NotFoundError("Test de niveau introuvable")
            return self._read(db, row)

    def find_request(self, learner_id: str, request_id: str) -> PlacementAttempt | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(PlacementAttemptRow).where(
                    PlacementAttemptRow.learner_id == learner_id,
                    PlacementAttemptRow.request_id == request_id,
                )
            )
            return self._read(db, row) if row else None

    def list(self, learner_id: str) -> tuple[PlacementAttempt, ...]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(PlacementAttemptRow)
                .where(PlacementAttemptRow.learner_id == learner_id)
                .order_by(PlacementAttemptRow.created_at.desc(), PlacementAttemptRow.id)
            )
            return tuple(self._read(db, row) for row in rows)

    def create(self, attempt: PlacementAttempt) -> PlacementAttempt:
        existing = self.find_request(attempt.learner_id, attempt.request_id)
        if existing:
            return existing
        try:
            with Session(self.engine) as db, db.begin():
                db.add(
                    PlacementAttemptRow(
                        id=attempt.id,
                        learner_id=attempt.learner_id,
                        request_id=attempt.request_id,
                        set_id=attempt.set_id,
                        set_version=attempt.set_version,
                        set_hash=attempt.set_hash,
                        status=attempt.status,
                        phase=attempt.phase,
                        state=attempt.state,
                        result=None,
                        created_at=attempt.created_at,
                        ended_at=None,
                    )
                )
        except IntegrityError:
            concurrent = self.find_request(attempt.learner_id, attempt.request_id)
            if concurrent:
                return concurrent
            raise
        return self.get(attempt.id, attempt.learner_id)

    def append_answer(
        self,
        attempt_id: str,
        learner_id: str,
        *,
        event_id: str,
        item_id: str,
        phase: str,
        payload: dict[str, Any],
        state: dict[str, Any],
        next_phase: str,
    ) -> PlacementAttempt:
        """Records one answer and the resulting state atomically; a replayed event is a no-op."""
        with Session(self.engine) as db, db.begin():
            row = self._lock(db, attempt_id, learner_id)
            current = self._read(db, row)
            duplicate = next((a for a in current.answers if a["event_id"] == event_id), None)
            if duplicate is not None:
                if duplicate["item_id"] != item_id:
                    raise InvalidStateError("Événement déjà utilisé pour un autre item")
                return current
            if current.status != "active":
                raise InvalidStateError("Ce test est terminé")
            db.add(
                PlacementAnswerRow(
                    attempt_id=attempt_id,
                    sequence=len(current.answers) + 1,
                    event_id=event_id,
                    item_id=item_id,
                    phase=phase,
                    payload=payload,
                )
            )
            row.state = state
            row.phase = next_phase
        return self.get(attempt_id, learner_id)

    def finish(self, attempt_id: str, learner_id: str, result: dict[str, Any]) -> PlacementAttempt:
        with Session(self.engine) as db, db.begin():
            row = self._lock(db, attempt_id, learner_id)
            if row.status == "completed":
                return self._read(db, row)
            row.status = "completed"
            row.phase = "completed"
            row.result = result
            row.ended_at = utc_now()
        return self.get(attempt_id, learner_id)
