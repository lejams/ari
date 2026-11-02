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

    def get(self, exercise_id: str) -> StoredExercise: ...

    def list_active(
        self, *, level: str | None = None, category: str | None = None
    ) -> tuple[StoredExercise, ...]: ...

    def withdraw(self, exercise_id: str, *, reason: str, actor: str) -> None: ...

    def add_review(self, exercise_id: str, review: ExerciseReview) -> None: ...

    def reviews(self, exercise_id: str) -> tuple[ExerciseReview, ...]: ...

    def dedup_keys(self) -> frozenset[str]: ...


class DrillStore(Protocol):
    def record(self, drill: LanguageDrill) -> LanguageDrill:
        """Keep the answer; the same learner and request return the answer already kept."""
        ...

    def of_learner(self, learner_id: str, *, limit: int = 200) -> tuple[LanguageDrill, ...]: ...
