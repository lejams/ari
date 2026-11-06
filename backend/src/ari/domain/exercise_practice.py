"""Serving the exercise bank to a learner: the working level per category and the draw.

Pure functions, no model call. Two versioned rules:

- `working-level-v1` (plan section 4.4, decision D8): each category has its own working level,
  which starts from the learner's level (the placement estimate, else the declared level, held
  between B1 and C1, the levels of the bank). It moves up one level once the last
  `LEVEL_WINDOW` answers at the current level are at least `UP_RATE` right, down one level when
  they are under `DOWN_RATE`; the count restarts after each move. Answers given before the last
  placement test are not replayed: a new estimate is a new start. The estimate itself is never
  overwritten.
- `exercise-draw-v1`: among the active exercises of a category at the working level, never one
  the learner already got right, nor one a learner reported and nobody has looked at since.
  Exercises never answered come first, in an order of their own for each learner (a hash, so
  the order is stable and two learners do not see the bank in the same order). Once they are
  all answered, the ones missed come back, the oldest miss first, at least `RETRY_AFTER` after
  the miss.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal

from ari.domain.language_drill import CATEGORY_LABELS, LanguageProfile
from ari.domain.language_exercises import (
    EXERCISE_CATEGORIES,
    EXERCISE_LEVELS,
    LanguageDrill,
    LanguageExercise,
)

WORKING_LEVEL_VERSION = "working-level-v1"
DRAW_VERSION = "exercise-draw-v1"
LEVEL_WINDOW = 10
UP_RATE = 0.8
DOWN_RATE = 0.5
RETRY_AFTER = timedelta(days=1)
SERIES_ITEMS = 5  # Exercises in one series: one programme slot.

FocusSource = Literal["sessions", "placement"]

_ORDER = ("A1", "A2", "B1", "B2", "C1", "C2")


def start_level(level_reference: str | None) -> str:
    """The learner's level held within the bank's levels; B1 when nothing is known yet."""
    if level_reference not in _ORDER:
        return EXERCISE_LEVELS[0]
    index = _ORDER.index(level_reference)
    lowest, highest = _ORDER.index(EXERCISE_LEVELS[0]), _ORDER.index(EXERCISE_LEVELS[-1])
    return _ORDER[min(max(index, lowest), highest)]


def working_level(
    start: str, answers: Iterable[LanguageDrill], *, since: datetime | None = None
) -> str:
    """The working level of one category, replayed from the learner's answers (any order)."""
    level = start
    window: list[bool] = []
    for answer in sorted(answers, key=lambda a: (a.answered_at, a.id)):
        if since is not None and answer.answered_at < since:
            continue
        if answer.level != level:
            continue  # Answered at another level: it says nothing about this one.
        window.append(answer.correct)
        if len(window) < LEVEL_WINDOW:
            continue
        rate = sum(window[-LEVEL_WINDOW:]) / LEVEL_WINDOW
        index = EXERCISE_LEVELS.index(level)
        if rate >= UP_RATE and index + 1 < len(EXERCISE_LEVELS):
            level, window = EXERCISE_LEVELS[index + 1], []
        elif rate < DOWN_RATE and index > 0:
            level, window = EXERCISE_LEVELS[index - 1], []
    return level


def working_levels(
    start: str, answers: Sequence[LanguageDrill], *, since: datetime | None = None
) -> dict[str, str]:
    return {
        category: working_level(start, (a for a in answers if a.category == category), since=since)
        for category in EXERCISE_CATEGORIES
    }


def _order(learner_id: str, exercise_id: str) -> str:
    return sha256(f"{DRAW_VERSION}\x1f{learner_id}\x1f{exercise_id}".encode()).hexdigest()


def drawable(
    exercises: Iterable[LanguageExercise],
    answers: Sequence[LanguageDrill],
    learner_id: str,
    now: datetime,
    *,
    held: frozenset[str] = frozenset(),
) -> list[LanguageExercise]:
    """The exercises the learner may be given, in the order they would be given.

    `exercises` are the active ones of one category and level; `held` the ones a learner
    reported and nobody has reviewed since.
    """
    right = {a.exercise_id for a in answers if a.correct}
    last_miss: dict[str, datetime] = {}
    for answer in answers:
        if not answer.correct:
            last_miss[answer.exercise_id] = max(
                last_miss.get(answer.exercise_id, answer.answered_at), answer.answered_at
            )
    fresh: list[LanguageExercise] = []
    retry: list[tuple[datetime, LanguageExercise]] = []
    for exercise in exercises:
        identifier = exercise.exercise_id
        if identifier in held or identifier in right:
            continue
        missed = last_miss.get(identifier)
        if missed is None:
            fresh.append(exercise)
        elif now - missed >= RETRY_AFTER:
            retry.append((missed, exercise))
    fresh.sort(key=lambda e: _order(learner_id, e.exercise_id))
    retry.sort(key=lambda pair: (pair[0], pair[1].exercise_id))
    return [*fresh, *(exercise for _, exercise in retry)]


@dataclass(frozen=True, slots=True)
class FocusCandidate:
    """A category to work on, and the fact that makes it one."""

    category: str
    source: FocusSource
    errors: int

    def rationale(self, level: str) -> str:
        label = CATEGORY_LABELS[self.category]
        if self.source == "sessions":
            return (
                f"Exercices sur {label}, niveau {level} : {self.errors} erreurs dans vos "
                "dernières séances."
            )
        return f"Exercices sur {label}, niveau {level} : un point faible de votre test de niveau."


def focus_candidates(
    profile: LanguageProfile, weak_points: Sequence[dict[str, Any]]
) -> list[FocusCandidate]:
    """Categories to work on, in order: recurring errors of the recent sessions (two or more),
    then the weak points of the placement test. Empty when nothing is established: no exercise
    is planned on a guess."""
    candidates: list[FocusCandidate] = []
    for category in profile.recurring:
        if category in EXERCISE_CATEGORIES:
            errors = profile.errors_by_category.get(category, 0)
            candidates.append(FocusCandidate(category, "sessions", errors))
    seen = {c.category for c in candidates}
    for point in weak_points:
        named = point.get("category")
        if isinstance(named, str) and named in EXERCISE_CATEGORIES and named not in seen:
            seen.add(named)
            errors = int(point.get("written_wrong", 0)) + int(point.get("spoken_errors", 0))
            candidates.append(FocusCandidate(named, "placement", errors))
    return candidates


def report_open(reviews: Iterable[tuple[str, str]], reviewer_name: str) -> bool:
    """This reviewer's report on the exercise is still open: no person has reviewed it since.

    `reviews` are the exercise's (reviewer_kind, reviewer_name) pairs, oldest first.
    """
    open_ = False
    for kind, name in reviews:
        if kind == "human":
            open_ = False
        elif kind == "learner_report" and name == reviewer_name:
            open_ = True
    return open_


def series_completions(answers: Iterable[LanguageDrill], since: datetime) -> list[datetime]:
    """When each series of `SERIES_ITEMS` answers given since `since` was completed."""
    times = sorted(a.answered_at for a in answers if a.answered_at >= since)
    return [times[i] for i in range(SERIES_ITEMS - 1, len(times), SERIES_ITEMS)]
