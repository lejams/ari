"""Owned, immutable-content exercise attempts with atomic/idempotent mutations."""

import json
from datetime import UTC, datetime

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.practice import PracticeAnswer, PracticeContent, PracticeFeedback, PracticeRun
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.persistence.clinical_rows import ScenarioRow
from ari.infrastructure.persistence.practice_rows import PracticeAnswerRow, PracticeRunRow


def _date(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SqlPracticeRepository:
    def __init__(self, engine: Engine, *, allow_synthetic: bool = False) -> None:
        self.engine, self.allow_synthetic = engine, allow_synthetic

    def _read(self, db: Session, row: PracticeRunRow) -> PracticeRun:
        content = PracticeContent.model_validate_json(json.dumps(row.content))
        if content.content_hash != row.content_hash:
            raise InvalidStateError("Le contenu épinglé de l'exercice est altéré")
        answers = []
        for index, answer_row in enumerate(
            db.scalars(
                select(PracticeAnswerRow)
                .where(PracticeAnswerRow.run_id == row.id)
                .order_by(PracticeAnswerRow.sequence)
            ),
            1,
        ):
            answer = PracticeAnswer.model_validate_json(json.dumps(answer_row.payload))
            if (answer.event_id, answer.question_id, index) != (
                answer_row.event_id,
                answer_row.question_id,
                answer_row.sequence,
            ):
                raise InvalidStateError("Métadonnées de réponse incohérentes")
            answers.append(answer.model_dump(mode="json"))
        return PracticeRun.model_validate_json(
            json.dumps(
                {
                    "id": row.id,
                    "learner_id": row.learner_id,
                    "request_id": row.request_id,
                    "content": content.model_dump(mode="json"),
                    "mode": row.mode,
                    "status": row.status,
                    "created_at": _date(row.created_at).isoformat(),
                    "ended_at": _date(row.ended_at).isoformat() if row.ended_at else None,
                    "answers": answers,
                    "feedback": row.feedback,
                }
            )
        )

    def _lock(self, db: Session, run_id: str, learner_id: str) -> PracticeRunRow:
        # Acquires a SQLite write lock / PostgreSQL row lock before inspecting responses.
        db.execute(
            update(PracticeRunRow)
            .where(
                PracticeRunRow.id == run_id,
                PracticeRunRow.learner_id == learner_id,
            )
            .values(status=PracticeRunRow.status)
        )
        row = db.get(PracticeRunRow, run_id)
        if row is None or row.learner_id != learner_id:
            raise NotFoundError("Exercice introuvable")
        return row

    def find_request(self, learner_id: str, request_id: str) -> PracticeRun | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(PracticeRunRow).where(
                    PracticeRunRow.learner_id == learner_id,
                    PracticeRunRow.request_id == request_id,
                )
            )
            return self._read(db, row) if row else None

    def get(self, run_id: str, learner_id: str) -> PracticeRun:
        with Session(self.engine) as db:
            row = db.scalar(
                select(PracticeRunRow).where(
                    PracticeRunRow.id == run_id,
                    PracticeRunRow.learner_id == learner_id,
                )
            )
            if row is None:
                raise NotFoundError("Exercice introuvable")
            return self._read(db, row)

    def list(self, learner_id: str) -> tuple[PracticeRun, ...]:
        with Session(self.engine) as db:
            return tuple(
                self._read(db, row)
                for row in db.scalars(
                    select(PracticeRunRow)
                    .where(PracticeRunRow.learner_id == learner_id)
                    .order_by(PracticeRunRow.created_at.desc(), PracticeRunRow.id)
                )
            )

    @staticmethod
    def _same_request(existing: PracticeRun, proposed: PracticeRun) -> PracticeRun:
        if (
            existing.mode != proposed.mode
            or existing.content.content_hash != proposed.content.content_hash
        ):
            raise InvalidStateError("Clé de démarrage réutilisée avec un autre exercice ou mode")
        return existing

    def create(self, run: PracticeRun) -> PracticeRun:
        run = PracticeRun.model_validate_json(run.model_dump_json())
        if run.answers or run.feedback is not None or run.status != "active":
            raise InvalidStateError("Un exercice doit commencer vide et actif")
        existing = self.find_request(run.learner_id, run.request_id)
        if existing:
            return self._same_request(existing, run)
        try:
            with Session(self.engine) as db, db.begin():
                if run.content.provenance == "synthetic_demo":
                    if not self.allow_synthetic:
                        raise InvalidStateError("Démonstrations désactivées")
                else:
                    db.execute(
                        update(ScenarioRow)
                        .where(
                            ScenarioRow.id == run.content.scenario_id,
                            ScenarioRow.version == run.content.scenario_version,
                        )
                        .values(status=ScenarioRow.status)
                    )
                    row = db.get(
                        ScenarioRow, (run.content.scenario_id, run.content.scenario_version)
                    )
                    if row is None or row.status != "published":
                        raise InvalidStateError("Scénario non publié ou retiré")
                    bundle = ClinicalStore(self.engine)._bundle(db, row)
                    if bundle.content_hash != run.content.bundle.content_hash:
                        raise InvalidStateError("Contenu différent de la publication")
                db.add(
                    PracticeRunRow(
                        id=run.id,
                        learner_id=run.learner_id,
                        request_id=run.request_id,
                        mode=run.mode,
                        status=run.status,
                        content_hash=run.content.content_hash,
                        content=run.content.model_dump(mode="json"),
                        feedback=None,
                        created_at=run.created_at,
                        ended_at=None,
                    )
                )
        except IntegrityError:
            concurrent = self.find_request(run.learner_id, run.request_id)
            if concurrent:
                return self._same_request(concurrent, run)
            raise
        return self.get(run.id, run.learner_id)

    def append_answer(self, run_id: str, learner_id: str, answer: PracticeAnswer) -> PracticeRun:
        answer = PracticeAnswer.model_validate_json(answer.model_dump_json())
        with Session(self.engine) as db, db.begin():
            row = self._lock(db, run_id, learner_id)
            run = self._read(db, row)
            duplicate = next((a for a in run.answers if a.event_id == answer.event_id), None)
            if duplicate:
                if (duplicate.question_id, duplicate.text) != (answer.question_id, answer.text):
                    raise InvalidStateError("Événement déjà utilisé avec une autre réponse")
                return run
            if run.status != "active":
                raise InvalidStateError("Reprendre l'exercice avant de répondre; fin immutable")
            specification = run.content.bundle.scenarios[0].practice
            if specification is None or len(run.answers) >= len(specification.questions):
                raise InvalidStateError("Toutes les questions ont déjà une réponse")
            if answer.question_id != specification.questions[len(run.answers)].id:
                raise InvalidStateError("Question inattendue ou déjà répondue")
            db.add(
                PracticeAnswerRow(
                    run_id=run_id,
                    question_id=answer.question_id,
                    event_id=answer.event_id,
                    sequence=len(run.answers) + 1,
                    payload=answer.model_dump(mode="json"),
                )
            )
        return self.get(run_id, learner_id)

    def set_paused(self, run_id: str, learner_id: str, paused: bool) -> PracticeRun:
        with Session(self.engine) as db, db.begin():
            row = self._lock(db, run_id, learner_id)
            if row.status == "completed":
                raise InvalidStateError("Un exercice terminé ne peut pas reprendre")
            self._read(db, row)
            row.status = "paused" if paused else "active"
        return self.get(run_id, learner_id)

    def finish(
        self, run_id: str, learner_id: str, feedback: PracticeFeedback, expected_answer_count: int
    ) -> PracticeRun:
        with Session(self.engine) as db, db.begin():
            row = self._lock(db, run_id, learner_id)
            run = self._read(db, row)
            if run.status == "completed":
                return run
            if len(run.answers) != expected_answer_count:
                raise InvalidStateError(
                    "Une réponse vient d'arriver; relancer le calcul du feedback"
                )
            ended_at = datetime.now(UTC)
            candidate = run.model_copy(
                update={
                    "status": "completed",
                    "feedback": feedback,
                    "ended_at": ended_at,
                }
            )
            # Validate the entire domain transition before committing immutable feedback.
            PracticeRun.model_validate_json(candidate.model_dump_json())
            row.feedback = feedback.model_dump(mode="json")
            row.ended_at = ended_at
            row.status = "completed"
        return self.get(run_id, learner_id)
