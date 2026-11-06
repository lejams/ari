"""SQL implementation of the exercise bank and of the drill store."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.exercise_practice import report_open
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


# First key of the advisory lock that serialises one learner's reports (the second is the name).
_REPORT_LOCK_SPACE = 7_301


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
        return self.add_many([(exercise, reviews)], batch_id=batch_id, provenance=provenance)[0]

    def add_many(
        self,
        items: Sequence[tuple[LanguageExercise, Sequence[ExerciseReview]]],
        *,
        batch_id: str,
        provenance: dict[str, Any],
    ) -> list[bool]:
        """All or nothing: one transaction for the whole batch."""
        checked = [(LanguageExercise.model_validate_json(e.model_dump_json()), r) for e, r in items]
        try:
            with Session(self.engine) as db, db.begin():
                return [
                    self._add_in(db, exercise, reviews, batch_id, provenance)
                    for exercise, reviews in checked
                ]
        except IntegrityError as exc:
            raise InvalidStateError(f"Exercice refusé par la base : {exc.orig}") from exc

    @staticmethod
    def _add_in(
        db: Session,
        exercise: LanguageExercise,
        reviews: Sequence[ExerciseReview],
        batch_id: str,
        provenance: dict[str, Any],
    ) -> bool:
        existing = db.get(LanguageExerciseRow, exercise.exercise_id)
        if existing is not None:
            return False  # Same content, same id: already in the bank.
        clash = db.scalar(
            select(LanguageExerciseRow.id).where(
                LanguageExerciseRow.dedup_key == exercise.dedup_key()
            )
        )
        if clash is not None:
            raise InvalidStateError(f"Cette phrase correcte est déjà dans la banque ({clash})")
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

    def search(
        self,
        *,
        status: str | None = None,
        level: str | None = None,
        category: str | None = None,
        skill: str | None = None,
        format: str | None = None,
        ids: frozenset[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[tuple[StoredExercise, ...], int]:
        """The exercises matching every given filter (newest first), and how many match in all."""
        filters = [
            column == value
            for column, value in (
                (LanguageExerciseRow.status, status),
                (LanguageExerciseRow.level, level),
                (LanguageExerciseRow.category, category),
                (LanguageExerciseRow.skill, skill),
                (LanguageExerciseRow.format, format),
            )
            if value is not None
        ]
        if ids is not None:
            filters.append(LanguageExerciseRow.id.in_(sorted(ids)))
        with Session(self.engine) as db:
            total = db.scalar(select(func.count()).select_from(LanguageExerciseRow).where(*filters))
            rows = db.scalars(
                select(LanguageExerciseRow)
                .where(*filters)
                .order_by(LanguageExerciseRow.created_at.desc(), LanguageExerciseRow.id)
                .limit(limit)
                .offset(offset)
            )
            return tuple(_stored(row) for row in rows), int(total or 0)

    def counts(self) -> dict[str, dict[str, int]]:
        """How many exercises there are for each value of each filter, all statuses together."""
        columns = {
            "status": LanguageExerciseRow.status,
            "level": LanguageExerciseRow.level,
            "category": LanguageExerciseRow.category,
            "skill": LanguageExerciseRow.skill,
            "format": LanguageExerciseRow.format,
        }
        with Session(self.engine) as db:
            return {
                name: {
                    str(value): int(count)
                    for value, count in db.execute(select(column, func.count()).group_by(column))
                }
                for name, column in columns.items()
            }

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

    def add_report(
        self, exercise_id: str, review: ExerciseReview, *, cap: int, since: datetime
    ) -> Literal["added", "open", "capped"]:
        with Session(self.engine) as db, db.begin():
            # One reviewer at a time: the open-report check, the count and the insert below run
            # in this transaction, so two simultaneous reports cannot both pass the cap.
            db.execute(
                text("SELECT pg_advisory_xact_lock(CAST(:space AS integer), hashtext(:name))"),
                {"space": _REPORT_LOCK_SPACE, "name": review.reviewer_name},
            )
            if db.get(LanguageExerciseRow, exercise_id) is None:
                raise NotFoundError(f"Exercice introuvable : {exercise_id}")
            history = db.execute(
                select(
                    LanguageExerciseReviewRow.reviewer_kind, LanguageExerciseReviewRow.reviewer_name
                )
                .where(LanguageExerciseReviewRow.exercise_id == exercise_id)
                .order_by(LanguageExerciseReviewRow.sequence)
            )
            if report_open(((kind, name) for kind, name in history), review.reviewer_name):
                return "open"
            count = db.scalar(
                select(func.count())
                .select_from(LanguageExerciseReviewRow)
                .where(
                    LanguageExerciseReviewRow.reviewer_name == review.reviewer_name,
                    LanguageExerciseReviewRow.created_at >= since,
                )
            )
            if int(count or 0) >= cap:
                return "capped"
            db.add(_review_row(exercise_id, review))
            return "added"

    def held_for_review(self) -> frozenset[str]:
        """Active exercises whose last word is a learner's report: no person has looked since.

        A person's review after the report (put back in circulation, or withdrawn) settles it.
        """
        with Session(self.engine) as db:
            rows = db.execute(
                select(
                    LanguageExerciseReviewRow.exercise_id, LanguageExerciseReviewRow.reviewer_kind
                )
                .join(
                    LanguageExerciseRow,
                    LanguageExerciseRow.id == LanguageExerciseReviewRow.exercise_id,
                )
                .where(
                    LanguageExerciseRow.status == "active",
                    LanguageExerciseReviewRow.reviewer_kind.in_(("learner_report", "human")),
                )
                .order_by(LanguageExerciseReviewRow.sequence)
            )
            last: dict[str, str] = {}
            for exercise_id, kind in rows:
                last[exercise_id] = kind
        return frozenset(e for e, kind in last.items() if kind == "learner_report")


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
