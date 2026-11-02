"""SQL implementation of the exercise bank and of the drill store."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.language_exercises import (
    ExerciseReview,
    LanguageDrill,
    LanguageExercise,
    StoredExercise,
)
from ari.domain.models import utc_now
from ari.infrastructure.persistence.platform.exercise_rows import (
    LanguageDrillRow,
    LanguageExerciseReviewRow,
    LanguageExerciseRow,
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _stored(row: LanguageExerciseRow) -> StoredExercise:
    exercise = LanguageExercise.model_validate_json(json.dumps(row.payload))
    if exercise.content_hash != row.content_hash or exercise.exercise_id != row.id:
        raise InvalidStateError(f"Le contenu de l'exercice {row.id} est altéré")
    return StoredExercise(
        exercise=exercise,
        batch_id=row.batch_id,
        provenance=row.provenance,
        status=cast(Literal["active", "withdrawn"], row.status),
        created_at=_aware(row.created_at),
        withdrawn_reason=row.withdrawn_reason,
    )


def _review_row(exercise_id: str, review: ExerciseReview) -> LanguageExerciseReviewRow:
    return LanguageExerciseReviewRow(
        exercise_id=exercise_id,
        reviewer_kind=review.reviewer_kind,
        reviewer_name=review.reviewer_name,
        decision=review.decision,
        payload=review.payload,
        created_at=review.created_at,
    )


class SqlExerciseBank:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def add(
        self,
        exercise: LanguageExercise,
        *,
        batch_id: str,
        provenance: dict[str, Any],
        reviews: Sequence[ExerciseReview],
    ) -> bool:
        exercise = LanguageExercise.model_validate_json(exercise.model_dump_json())
        try:
            with Session(self.engine) as db, db.begin():
                existing = db.get(LanguageExerciseRow, exercise.exercise_id)
                if existing is not None:
                    return False  # Same content, same id: already in the bank.
                clash = db.scalar(
                    select(LanguageExerciseRow.id).where(
                        LanguageExerciseRow.dedup_key == exercise.dedup_key()
                    )
                )
                if clash is not None:
                    raise InvalidStateError(
                        f"Cette phrase correcte est déjà dans la banque ({clash})"
                    )
                db.add(
                    LanguageExerciseRow(
                        id=exercise.exercise_id,
                        content_hash=exercise.content_hash,
                        dedup_key=exercise.dedup_key(),
                        schema_version=exercise.schema_version,
                        level=exercise.level,
                        category=exercise.category,
                        skill=exercise.skill,
                        format=exercise.format,
                        payload=exercise.model_dump(mode="json"),
                        batch_id=batch_id,
                        provenance=provenance,
                        created_at=utc_now(),
                        status="active",
                    )
                )
                db.flush()
                for review in reviews:
                    db.add(_review_row(exercise.exercise_id, review))
        except IntegrityError as exc:
            raise InvalidStateError(f"Exercice refusé par la base : {exc.orig}") from exc
        return True

    def get(self, exercise_id: str) -> StoredExercise:
        with Session(self.engine) as db:
            row = db.get(LanguageExerciseRow, exercise_id)
            if row is None:
                raise NotFoundError(f"Exercice introuvable : {exercise_id}")
            return _stored(row)

    def list_active(
        self, *, level: str | None = None, category: str | None = None
    ) -> tuple[StoredExercise, ...]:
        query = select(LanguageExerciseRow).where(LanguageExerciseRow.status == "active")
        if level is not None:
            query = query.where(LanguageExerciseRow.level == level)
        if category is not None:
            query = query.where(LanguageExerciseRow.category == category)
        with Session(self.engine) as db:
            rows = db.scalars(
                query.order_by(LanguageExerciseRow.created_at, LanguageExerciseRow.id)
            )
            return tuple(_stored(row) for row in rows)

    def withdraw(self, exercise_id: str, *, reason: str, actor: str) -> None:
        if not reason.strip() or not actor.strip():
            raise InvalidStateError("Un retrait donne son motif et son auteur")
        with Session(self.engine) as db, db.begin():
            row = db.get(LanguageExerciseRow, exercise_id)
            if row is None:
                raise NotFoundError(f"Exercice introuvable : {exercise_id}")
            if row.status == "withdrawn":
                raise InvalidStateError(f"{exercise_id} est déjà retiré")
            row.status = "withdrawn"
            row.withdrawn_reason = reason.strip()
            row.withdrawn_at = utc_now()
            db.flush()
            db.add(
                _review_row(
                    exercise_id,
                    ExerciseReview("human", actor.strip(), "reject", {"withdrawn": reason.strip()}),
                )
            )

    def add_review(self, exercise_id: str, review: ExerciseReview) -> None:
        with Session(self.engine) as db, db.begin():
            if db.get(LanguageExerciseRow, exercise_id) is None:
                raise NotFoundError(f"Exercice introuvable : {exercise_id}")
            db.add(_review_row(exercise_id, review))

    def reviews(self, exercise_id: str) -> tuple[ExerciseReview, ...]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(LanguageExerciseReviewRow)
                .where(LanguageExerciseReviewRow.exercise_id == exercise_id)
                .order_by(LanguageExerciseReviewRow.sequence)
            )
            return tuple(
                ExerciseReview(
                    reviewer_kind=cast(Any, row.reviewer_kind),
                    reviewer_name=row.reviewer_name,
                    decision=cast(Any, row.decision),
                    payload=row.payload,
                    created_at=_aware(row.created_at),
                )
                for row in rows
            )

    def dedup_keys(self) -> frozenset[str]:
        with Session(self.engine) as db:
            return frozenset(db.scalars(select(LanguageExerciseRow.dedup_key)))


class SqlDrillStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(self, drill: LanguageDrill) -> LanguageDrill:
        try:
            with Session(self.engine) as db, db.begin():
                existing = db.scalar(
                    select(LanguageDrillRow).where(
                        LanguageDrillRow.learner_id == drill.learner_id,
                        LanguageDrillRow.request_id == drill.request_id,
                    )
                )
                if existing is not None:
                    return _drill(existing)
                db.add(
                    LanguageDrillRow(
                        id=drill.id,
                        learner_id=drill.learner_id,
                        request_id=drill.request_id,
                        exercise_id=drill.exercise_id,
                        exercise_hash=drill.exercise_hash,
                        level=drill.level,
                        category=drill.category,
                        chosen_index=drill.chosen_index,
                        correct=drill.correct,
                        answered_at=drill.answered_at,
                    )
                )
        except IntegrityError as exc:
            raise InvalidStateError(f"Réponse refusée par la base : {exc.orig}") from exc
        return drill

    def of_learner(self, learner_id: str, *, limit: int = 200) -> tuple[LanguageDrill, ...]:
        with Session(self.engine) as db:
            rows = db.scalars(
                select(LanguageDrillRow)
                .where(LanguageDrillRow.learner_id == learner_id)
                .order_by(LanguageDrillRow.answered_at.desc(), LanguageDrillRow.id)
                .limit(limit)
            )
            return tuple(_drill(row) for row in rows)


def _drill(row: LanguageDrillRow) -> LanguageDrill:
    return LanguageDrill(
        id=row.id,
        learner_id=row.learner_id,
        request_id=row.request_id,
        exercise_id=row.exercise_id,
        exercise_hash=row.exercise_hash,
        level=row.level,
        category=row.category,
        chosen_index=row.chosen_index,
        correct=row.correct,
        answered_at=_aware(row.answered_at),
    )
