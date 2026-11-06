"""Language exercises for the learner: the bank drawn at the learner's working level.

The bank is generated and checked offline (`exercises_cli`, docs/LANGUAGE_EXERCISES.md); this
service only reads it. Rules in `domain/exercise_practice.py`: `working-level-v1` sets the level
of each category, `exercise-draw-v1` picks the next exercise. A learner's report holds the
exercise back from every learner until a person looks at it in the back-office.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from ari.application.ports.exercises import DrillStore, ExerciseBank
from ari.application.ports.placement import PlacementAttemptRepository
from ari.application.ports.repository import SessionRepository
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.exercise_practice import (
    DRAW_VERSION,
    SERIES_ITEMS,
    WORKING_LEVEL_VERSION,
    drawable,
    focus_candidates,
    series_completions,
    start_level,
    working_levels,
)
from ari.domain.language_drill import CATEGORY_LABELS, language_profile
from ari.domain.language_exercises import (
    EXERCISE_CATEGORIES,
    ExerciseReview,
    LanguageDrill,
    LanguageExercise,
    grade,
)
from ari.domain.models import LearnerProfile, new_id, utc_now

ReportReason = Literal["wrong_key", "several_answers", "unclear", "other"]
REPORT_REASONS: dict[str, str] = {
    "wrong_key": "La réponse attendue est fausse",
    "several_answers": "Plusieurs réponses sont justes",
    "unclear": "La phrase est incorrecte ou incompréhensible",
    "other": "Autre",
}
ANSWER_HISTORY = 10_000  # Every answer counts: one answered right is never asked again.
# A report holds an exercise back from everyone: one account cannot empty the bank in a day.
REPORTS_PER_DAY = 10

LIMITATIONS = (
    "Exercices écrits par une IA puis vérifiés par une autre IA (résolution sans la réponse et "
    "relecture selon une grille stricte), sans relecture humaine de chaque exercice. Si un "
    "exercice vous paraît faux, signalez-le : il est retiré pour tout le monde jusqu'à "
    "vérification."
)


def _public(exercise: LanguageExercise) -> dict[str, Any]:
    """What the learner sees before answering: never the key nor the explanation."""
    return {
        "id": exercise.exercise_id,
        "level": exercise.level,
        "category": exercise.category,
        "category_label": CATEGORY_LABELS[exercise.category],
        "format": exercise.format,
        "instruction_fr": exercise.instruction_fr,
        "context_fr": exercise.context_fr,
        "stem": exercise.stem,
        "options": list(exercise.options),
    }


class ExercisePracticeService:
    def __init__(
        self,
        bank: ExerciseBank,
        answers: DrillStore,
        sessions: SessionRepository,
        attempts: PlacementAttemptRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.bank = bank
        self.answers = answers
        self._sessions = sessions
        self._attempts = attempts
        self._clock = clock

    # ----- reading ---------------------------------------------------------------------

    def _weak_points(self, learner: LearnerProfile) -> list[dict[str, Any]]:
        attempt_id = learner.details.placement_attempt_id
        if not attempt_id:
            return []
        try:
            attempt = self._attempts.get(attempt_id, learner.id)
        except NotFoundError:
            return []
        points = (attempt.result or {}).get("weak_points") or []
        return [p for p in points if isinstance(p, dict)]

    def _history(self, learner_id: str) -> tuple[LanguageDrill, ...]:
        return self.answers.of_learner(learner_id, limit=ANSWER_HISTORY)

    def _levels(
        self, learner: LearnerProfile, history: tuple[LanguageDrill, ...]
    ) -> dict[str, str]:
        details = learner.details
        reference = details.estimated_level or details.declared_level
        start = start_level(reference.value if reference else None)
        return working_levels(start, history, since=details.estimated_at)

    def _drawable(
        self,
        learner_id: str,
        category: str,
        level: str,
        history: tuple[LanguageDrill, ...],
        held: frozenset[str],
    ) -> list[LanguageExercise]:
        stored = self.bank.list_active(level=level, category=category)
        return drawable(
            (s.exercise for s in stored),
            [a for a in history if a.category == category],
            learner_id,
            self._clock(),
            held=held,
        )

    def focus(self, learner_id: str) -> dict[str, Any] | None:
        """The category the programme works on this week, with exercises left to draw."""
        learner = self._sessions.get_learner(learner_id)
        history = self._history(learner_id)
        levels = self._levels(learner, history)
        held = self.bank.held_for_review()
        profile = language_profile(self._sessions.list_sessions(learner_id))
        for candidate in focus_candidates(profile, self._weak_points(learner)):
            level = levels[candidate.category]
            available = len(self._drawable(learner_id, candidate.category, level, history, held))
            if available:
                return {
                    "category": candidate.category,
                    "label": CATEGORY_LABELS[candidate.category],
                    "level": level,
                    "source": candidate.source,
                    "errors": candidate.errors,
                    "available": available,
                    "items": min(SERIES_ITEMS, available),
                    "rationale": candidate.rationale(level),
                }
        return None

    def overview(self, learner_id: str) -> dict[str, Any]:
        learner = self._sessions.get_learner(learner_id)
        history = self._history(learner_id)
        levels = self._levels(learner, history)
        held = self.bank.held_for_review()
        categories = []
        for category in EXERCISE_CATEGORIES:
            answered = [a for a in history if a.category == category]
            categories.append(
                {
                    "category": category,
                    "label": CATEGORY_LABELS[category],
                    "level": levels[category],
                    "available": len(
                        self._drawable(learner_id, category, levels[category], history, held)
                    ),
                    "answered": len(answered),
                    "correct": sum(1 for a in answered if a.correct),
                }
            )
        return {
            "versions": {"working_level": WORKING_LEVEL_VERSION, "draw": DRAW_VERSION},
            "series_items": SERIES_ITEMS,
            "categories": categories,
            "focus": self.focus(learner_id),
            "limitations": LIMITATIONS,
        }

    def next(self, learner_id: str, category: str) -> dict[str, Any]:
        if category not in EXERCISE_CATEGORIES:
            raise InvalidStateError("Catégorie inconnue")
        learner = self._sessions.get_learner(learner_id)
        history = self._history(learner_id)
        level = self._levels(learner, history)[category]
        queue = self._drawable(learner_id, category, level, history, self.bank.held_for_review())
        return {
            "category": category,
            "label": CATEGORY_LABELS[category],
            "level": level,
            "remaining": len(queue),
            "exercise": _public(queue[0]) if queue else None,
        }

    def completions(self, learner_id: str, since: datetime) -> list[datetime]:
        """When the learner finished each series of exercises since `since` (programme slots)."""
        return series_completions(self._history(learner_id), since)

    # ----- writing ---------------------------------------------------------------------

    def answer(
        self, learner_id: str, *, request_id: str, exercise_id: str, chosen_index: int
    ) -> dict[str, Any]:
        stored = self.bank.get(exercise_id)
        exercise = stored.exercise
        if stored.status != "active":
            raise InvalidStateError("Cet exercice a été retiré de la banque")
        if not 0 <= chosen_index < len(exercise.options):
            raise InvalidStateError("Réponse hors des options proposées")
        kept = self.answers.record(
            LanguageDrill(
                id=new_id(),
                learner_id=learner_id,
                request_id=request_id,
                exercise_id=exercise.exercise_id,
                exercise_hash=exercise.content_hash,
                level=exercise.level,
                category=exercise.category,
                chosen_index=chosen_index,
                correct=grade(exercise, chosen_index),
                answered_at=self._clock(),
            )
        )
        if kept.exercise_id != exercise.exercise_id:
            raise InvalidStateError("Cette requête a déjà servi à répondre à un autre exercice")
        return {
            "exercise": _public(exercise),
            "chosen_index": kept.chosen_index,
            "correct": kept.correct,
            "answer_index": exercise.answer_index,
            "correct_sentence": exercise.correct_sentence(),
            "explanation_fr": exercise.explanation_fr,
        }

    def report(
        self, learner_id: str, exercise_id: str, *, reason: ReportReason, comment: str | None
    ) -> dict[str, Any]:
        """Hold the exercise back from every learner until a person reviews it."""
        self.bank.get(exercise_id)  # NotFoundError for an unknown exercise.
        name = f"learner:{learner_id}"
        already = False  # This learner's report is still open: a person has not looked since.
        for review in self.bank.reviews(exercise_id):
            if review.reviewer_kind == "human":
                already = False
            elif review.reviewer_kind == "learner_report" and review.reviewer_name == name:
                already = True
        if not already:
            since = self._clock() - timedelta(days=1)
            if self.bank.reviews_by(name, since=since) >= REPORTS_PER_DAY:
                raise InvalidStateError(
                    "Vous avez déjà signalé plusieurs exercices aujourd'hui : merci, ils seront "
                    "vérifiés. Réessayez demain."
                )
            self.bank.add_review(
                exercise_id,
                ExerciseReview(
                    "learner_report",
                    name,
                    "report",
                    {
                        "learner_id": learner_id,
                        "reason": reason,
                        "comment": (comment or "").strip() or None,
                    },
                    created_at=self._clock(),
                ),
            )
        return {"reported": True, "already_reported": already}
