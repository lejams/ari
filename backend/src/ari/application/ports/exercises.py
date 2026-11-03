from collections.abc import Sequence
from typing import Any, Protocol

from ari.domain.language_exercises import (
    ExerciseReview,
    LanguageDrill,
    LanguageExercise,
    StoredExercise,
)


class ExerciseBank(Protocol):
    """The bank of language exercises: content never edited, a withdrawal is final."""

    def add(
        self,
        exercise: LanguageExercise,
        *,
        batch_id: str,
        provenance: dict[str, Any],
        reviews: Sequence[ExerciseReview],
    ) -> bool:
        """Store the exercise with its checks. False when this exact exercise is already there."""
        ...

    def add_many(
        self,
        items: Sequence[tuple[LanguageExercise, Sequence[ExerciseReview]]],
        *,
        batch_id: str,
        provenance: dict[str, Any],
    ) -> list[bool]:
        """`add` for a whole batch in one transaction: all of it is written or none of it."""
        ...

    def get(self, exercise_id: str) -> StoredExercise: ...

    def list_active(
        self, *, level: str | None = None, category: str | None = None
    ) -> tuple[StoredExercise, ...]: ...

    def search(
        self,
        *,
        status: str | None = None,
        level: str | None = None,
        category: str | None = None,
        skill: str | None = None,
        format: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[tuple[StoredExercise, ...], int]:
        """Exercises matching every given filter, newest first, and the total that match."""
        ...

    def counts(self) -> dict[str, dict[str, int]]:
        """Exercises per value of status, level, category, skill and format."""
        ...

    def withdraw(self, exercise_id: str, *, reason: str, actor: str) -> None: ...

    def add_review(self, exercise_id: str, review: ExerciseReview) -> None: ...

    def reviews(self, exercise_id: str) -> tuple[ExerciseReview, ...]: ...

    def dedup_keys(self) -> frozenset[str]: ...


class DrillStore(Protocol):
    def record(self, drill: LanguageDrill) -> LanguageDrill:
        """Keep the answer; the same learner and request return the answer already kept."""
        ...

    def of_learner(self, learner_id: str, *, limit: int = 200) -> tuple[LanguageDrill, ...]: ...
