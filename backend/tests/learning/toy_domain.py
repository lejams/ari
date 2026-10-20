"""A toy domain of five skills, defined here and nowhere in `ari`: it proves the core runs a whole
cycle without a single line of FSP. Think of a small maths course."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ari.learning import Observation, SkillFramework
from ari.learning.model import ObservationKind

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

TOY_TAXONOMY: dict[str, Any] = {
    "id": "toy-maths",
    "version": "1",
    "skills": [
        {"id": "arithmetic", "label": "Arithmetic", "family": "number", "half_life_days": 60},
        {"id": "fractions", "label": "Fractions", "family": "number", "half_life_days": 45},
        {"id": "equations", "label": "Equations", "family": "algebra", "half_life_days": 45},
        {"id": "functions", "label": "Functions", "family": "algebra", "half_life_days": 30},
        {
            "id": "exam_rules",
            "label": "Exam rules",
            "family": "meta",
            "half_life_days": 30,
            "critical": True,
        },
    ],
    "edges": [
        {"from": "arithmetic", "to": "fractions", "kind": "prerequisite", "threshold": 0.6},
        {"from": "fractions", "to": "equations", "kind": "prerequisite", "threshold": 0.6},
        {"from": "equations", "to": "functions", "kind": "part_of"},
    ],
}


def toy_framework() -> SkillFramework:
    return SkillFramework.from_mapping(TOY_TAXONOMY)


_counter = 0


def obs(
    skill: str = "arithmetic",
    *,
    kind: ObservationKind = "success",
    outcome: float | None = None,
    reliability: float = 1.0,
    item: str = "item-1",
    attempt: str = "attempt-1",
    at: datetime = T0,
    days: float = 0.0,
    producer: str = "scorer-v1",
    source: str = "deterministic",
    evidence: Mapping[str, Any] | None = None,
    learner: str = "learner-1",
    id: str | None = None,
) -> Observation:
    """An observation with sensible defaults; `outcome` follows the kind unless given."""
    global _counter
    _counter += 1
    if outcome is None and kind not in {"not_elicited", "uninterpretable", "interrupted"}:
        outcome = 0.0 if kind in {"failure", "required_omission"} else 1.0
    return Observation(
        id=id or f"o{_counter:06d}",
        learner_id=learner,
        skill_id=skill,
        item_ref=item,
        attempt_ref=attempt,
        kind=kind,
        outcome=outcome,
        reliability=reliability,
        source=source,  # type: ignore[arg-type]
        producer=producer,
        evidence=dict(evidence or {}),
        observed_at=at + timedelta(days=days),
    )


@dataclass(frozen=True, slots=True)
class ToyAnswer:
    """What the toy course records when a pupil answers one exercise."""

    pupil: str
    exercise: str
    skills: tuple[str, ...]
    correct: bool
    attempt: str
    at: datetime


def toy_adapter(answer: ToyAnswer) -> list[Observation]:
    """The whole domain adapter of the toy course: a result in, observations out."""
    return [
        obs(
            skill,
            kind="success" if answer.correct else "failure",
            item=f"toy:{answer.exercise}",
            attempt=answer.attempt,
            at=answer.at,
            learner=answer.pupil,
            producer="toy-marking-v1",
        )
        for skill in answer.skills
    ]
