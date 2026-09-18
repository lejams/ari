"""Placement test authoring contract and the deterministic staircase rules.

Content enters through the registry like clinical cases: import, one linguistic review of
the exact hash, publication. Items are general German (or the development language), never
medical knowledge. The result is an estimate, never a certificate.
"""

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median_low
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from ari.domain.clinical import (
    ClinicalModel,
    Identifier,
    RawCaseSource,
    SourceRef,
    Text,
    VersionRef,
    unique,
)
from ari.domain.clinical_privacy import contact_locations
from ari.domain.models import utc_now

Level = Literal["A1", "A2", "B1", "B2", "C1", "C2"]
LEVELS: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")
REQUIRED_LEVELS: tuple[str, ...] = ("A1", "A2", "B1", "B2")
PLACEMENT_METHOD = "placement-staircase-v1"

# Staircase parameters (documented in docs/CLINICAL_CASES.md).
STAIRCASE_MAX_ITEMS = 14
STAIRCASE_MAX_REVERSALS = 3
STAIRCASE_STREAK = 2
ESTIMATE_WINDOW = 6
LISTENING_ITEMS = 4
SPEAKING_MIN_CONFIDENCE = 0.6


class McqItem(ClinicalModel):
    id: Identifier
    skill: Literal["vocabulary", "grammar"]
    level: Level
    stem: Text
    options: tuple[Text, ...] = Field(min_length=3, max_length=4)
    answer_index: Annotated[int, Field(ge=0, le=3)]
    rationale_fr: Text | None = None

    @model_validator(mode="after")
    def answer_exists(self) -> Self:
        if self.answer_index >= len(self.options):
            raise ValueError("answer_index hors des options")
        return self


class ListeningItem(ClinicalModel):
    id: Identifier
    level: Level
    script: Text  # Read aloud by the TTS provider; never shown before the answer.
    question: Text
    options: tuple[Text, ...] = Field(min_length=3, max_length=4)
    answer_index: Annotated[int, Field(ge=0, le=3)]

    @model_validator(mode="after")
    def answer_exists(self) -> Self:
        if self.answer_index >= len(self.options):
            raise ValueError("answer_index hors des options")
        return self


class SpeakingItem(ClinicalModel):
    id: Identifier
    level_hint: Level
    prompt_fr: Text
    prompt_target: Text  # The task in the test language, shown with the French cue.
    target_seconds: Annotated[int, Field(ge=30, le=180)] = 60


class PlacementSetVersion(VersionRef):
    schema_version: Literal["placement-set-v1"] = "placement-set-v1"
    language: Literal["de-DE", "fr-FR", "en-US"] = "de-DE"
    title: Text
    description_fr: Text
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    mcq_items: tuple[McqItem, ...] = Field(min_length=1)
    listening_items: tuple[ListeningItem, ...] = Field(min_length=1)
    speaking_items: tuple[SpeakingItem, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        unique(
            tuple(
                i.id
                for group in (self.mcq_items, self.listening_items, self.speaking_items)
                for i in group
            ),
            "items du test",
        )
        unique(tuple(s.source_id for s in self.sources), "sources")
        for level in REQUIRED_LEVELS:
            if sum(1 for item in self.mcq_items if item.level == level) < 4:
                raise ValueError(f"Au moins 4 QCM requis au niveau {level}")
            if not any(item.level == level for item in self.listening_items):
                raise ValueError(f"Au moins 1 item d'écoute requis au niveau {level}")
        return self

    def mcq_levels(self) -> tuple[str, ...]:
        return tuple(level for level in LEVELS if any(i.level == level for i in self.mcq_items))


class PlacementReview(ClinicalModel):
    id: Identifier
    set: VersionRef
    set_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    review_type: Literal["linguistic"] = "linguistic"
    reviewer_name: Text
    reviewed_at: datetime
    decision: Literal["approve", "request_changes", "reject"]
    notes: Text

    @model_validator(mode="after")
    def timezone_required(self) -> Self:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("La date de revue doit avoir un fuseau horaire")
        return self


class PlacementBundle(ClinicalModel):
    schema_version: Literal["ari-placement-bundle-v1"] = "ari-placement-bundle-v1"
    sources: tuple[RawCaseSource, ...] = Field(min_length=1)
    sets: tuple[PlacementSetVersion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self) -> Self:
        if contact_locations([s.model_dump(mode="json") for s in self.sets]):
            raise ValueError("Coordonnées personnelles détectées dans le test de niveau")
        unique(tuple(s.id for s in self.sources), "sources")
        unique(tuple(f"{s.id}@{s.version}" for s in self.sets), "sets")
        known = {s.id for s in self.sources}
        for placement_set in self.sets:
            if {ref.source_id for ref in placement_set.sources} - known:
                raise ValueError("Référence source inconnue dans un test de niveau")
        return self


# ----- deterministic rules ---------------------------------------------------------


def level_index(level: str) -> int:
    return LEVELS.index(level)


def shift_level(level: str, delta: int, available: tuple[str, ...] = LEVELS) -> str:
    """Move by `delta` steps within the levels that actually have items."""
    ordered = [lvl for lvl in LEVELS if lvl in available]
    position = min(
        range(len(ordered)), key=lambda i: abs(level_index(ordered[i]) - level_index(level))
    )
    return ordered[max(0, min(len(ordered) - 1, position + delta))]


def next_level(
    level: str, correct: bool, streak_correct: int, streak_wrong: int, available: tuple[str, ...]
) -> tuple[str, int, int, int]:
    """Staircase step. Returns (level, streak_correct, streak_wrong, direction).

    direction is +1 when the level went up, -1 when it went down, 0 when it stayed.
    """
    if correct:
        streak_correct, streak_wrong = streak_correct + 1, 0
        if streak_correct >= STAIRCASE_STREAK:
            moved = shift_level(level, 1, available)
            return moved, 0, 0, 1 if moved != level else 0
    else:
        streak_wrong, streak_correct = streak_wrong + 1, 0
        if streak_wrong >= STAIRCASE_STREAK:
            moved = shift_level(level, -1, available)
            return moved, 0, 0, -1 if moved != level else 0
    return level, streak_correct, streak_wrong, 0


def estimate_level(presented_levels: list[str], fallback: str) -> str:
    """Median (low) of the levels of the last items presented: robust to one lucky guess."""
    window = presented_levels[-ESTIMATE_WINDOW:]
    if not window:
        return fallback
    return LEVELS[median_low(level_index(level) for level in window)]


def listening_level(level: str, correct: int, total: int, available: tuple[str, ...]) -> str:
    if total == 0:
        return level
    ratio = correct / total
    if ratio >= 0.75:
        return shift_level(level, 1, available)
    if ratio >= 0.5:
        return level
    return shift_level(level, -1, available)


def band(
    vocab_grammar: str, listening: str, speaking: str | None, speaking_confidence: float
) -> tuple[str, bool]:
    """Overall estimate: the weakest measured skill; speaking counts only when confident."""
    levels = [vocab_grammar, listening]
    counted = speaking is not None and speaking_confidence >= SPEAKING_MIN_CONFIDENCE
    if counted and speaking is not None:
        levels.append(speaking)
    return LEVELS[min(level_index(level) for level in levels)], counted


@dataclass(frozen=True, slots=True)
class PlacementAttempt:
    """A learner's run of a published set: pinned content, staircase state, answers, result."""

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
