"""Remediation drills: owned, idempotent start and answers, answers appended under a row lock."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.language_drill import DrillAnswer, DrillItem, LanguageDrill
from ari.domain.models import utc_now
from ari.infrastructure.persistence.platform.language_drill_rows import LanguageDrillRow


def _dt(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _item_json(item: DrillItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "session_id": item.session_id,
        "category": item.category,
        "quote": item.quote,
        "correction": item.correction,
        "note": item.note,
        "turns": list(item.turns),
    }


def _answer_json(answer: DrillAnswer) -> dict[str, Any]:
    return {
        "event_id": answer.event_id,
        "item_id": answer.item_id,
        "text": answer.text,
        "outcome": answer.outcome,
        "submitted_at": answer.submitted_at.isoformat(),
    }


class SqlLanguageDrillRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _read(row: LanguageDrillRow) -> LanguageDrill:
        return LanguageDrill(
            id=row.id,
            learner_id=row.learner_id,
            request_id=row.request_id,
            version=row.version,
            focus_category=row.focus_category,
            status="completed" if row.status == "completed" else "active",
            items=tuple(
                DrillItem(
                    id=i["id"],
                    session_id=i["session_id"],
                    category=i["category"],
                    quote=i["quote"],
                    correction=i["correction"],
                    note=i["note"],
                    turns=tuple(i["turns"]),
                )
                for i in row.items
            ),
            answers=tuple(
                DrillAnswer(
                    event_id=a["event_id"],
                    item_id=a["item_id"],
                    text=a["text"],
                    outcome="matched" if a["outcome"] == "matched" else "not_matched",
                    submitted_at=datetime.fromisoformat(a["submitted_at"]),
                )
                for a in row.answers
            ),
            created_at=_dt(row.created_at),
            ended_at=_dt(row.ended_at) if row.ended_at else None,
        )

    @staticmethod
    def _lock(db: Session, drill_id: str, learner_id: str) -> LanguageDrillRow:
        # Row lock held until commit, so concurrent answers to the same drill serialise.
        row = db.scalar(
            select(LanguageDrillRow)
            .where(LanguageDrillRow.id == drill_id, LanguageDrillRow.learner_id == learner_id)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError("Exercice introuvable")
        return row

    def get(self, drill_id: str, learner_id: str) -> LanguageDrill:
        with Session(self.engine) as db:
            row = db.get(LanguageDrillRow, drill_id)
            if row is None or row.learner_id != learner_id:
                raise NotFoundError("Exercice introuvable")
            return self._read(row)

    def find_request(self, learner_id: str, request_id: str) -> LanguageDrill | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(LanguageDrillRow).where(
                    LanguageDrillRow.learner_id == learner_id,
                    LanguageDrillRow.request_id == request_id,
                )
            )
            return self._read(row) if row else None

    def _active(self, learner_id: str) -> LanguageDrill | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(LanguageDrillRow).where(
                    LanguageDrillRow.learner_id == learner_id, LanguageDrillRow.status == "active"
                )
            )
            return self._read(row) if row else None

    def list(self, learner_id: str) -> tuple[LanguageDrill, ...]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(LanguageDrillRow)
                .where(LanguageDrillRow.learner_id == learner_id)
                .order_by(LanguageDrillRow.created_at.desc(), LanguageDrillRow.id)
            )
            return tuple(self._read(row) for row in rows)

    def create(self, drill: LanguageDrill) -> LanguageDrill:
        if drill.answers or drill.status != "active" or not drill.items:
            raise InvalidStateError("Un exercice doit commencer vide, actif et avec des phrases")
        existing = self.find_request(drill.learner_id, drill.request_id)
        if existing:
            return existing
        try:
            with Session(self.engine) as db, db.begin():
                db.add(
                    LanguageDrillRow(
                        id=drill.id,
                        learner_id=drill.learner_id,
                        request_id=drill.request_id,
                        version=drill.version,
                        focus_category=drill.focus_category,
                        status=drill.status,
                        items=[_item_json(i) for i in drill.items],
                        answers=[],
                        created_at=drill.created_at,
                        ended_at=None,
                    )
                )
        except IntegrityError:
            # Lost a race: the same request, or another tab that started a drill for this learner
            # first (one unfinished drill per learner). Either way the winner is the answer.
            concurrent = self.find_request(drill.learner_id, drill.request_id) or self._active(
                drill.learner_id
            )
            if concurrent:
                return concurrent
            raise
        return self.get(drill.id, drill.learner_id)

    def append_answer(self, drill_id: str, learner_id: str, answer: DrillAnswer) -> LanguageDrill:
        """Records one answer atomically; a replayed event is a no-op."""
        with Session(self.engine) as db, db.begin():
            row = self._lock(db, drill_id, learner_id)
            current = self._read(row)
            duplicate = next((a for a in current.answers if a.event_id == answer.event_id), None)
            if duplicate is not None:
                if (duplicate.item_id, duplicate.text) != (answer.item_id, answer.text):
                    raise InvalidStateError("Événement déjà utilisé avec une autre réponse")
                return current
            if current.status != "active":
                raise InvalidStateError("Cet exercice est terminé")
            if len(current.answers) >= len(current.items) or (
                current.items[len(current.answers)].id != answer.item_id
            ):
                raise InvalidStateError("Phrase inattendue ou déjà traitée")
            row.answers = [*row.answers, _answer_json(answer)]
        return self.get(drill_id, learner_id)

    def finish(self, drill_id: str, learner_id: str) -> LanguageDrill:
        with Session(self.engine) as db, db.begin():
            row = self._lock(db, drill_id, learner_id)
            if row.status == "completed":
                return self._read(row)
            if not row.answers:
                raise InvalidStateError("Répondez à au moins une phrase avant de terminer")
            row.status = "completed"
            row.ended_at = utc_now()
        return self.get(drill_id, learner_id)
