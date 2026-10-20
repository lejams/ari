"""The vocabulary of the learning core: observations in, mastery estimates out.

Nothing here knows a domain. A skill is an opaque identifier with a half-life; an item is an
opaque reference; an observation says what one attempt showed about one skill, how reliable
that evidence is, and who produced it. Domains (the FSP, a maths course) translate their own
results into observations through an adapter that lives outside this package.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any, Literal

ObservationKind = Literal[
    "success",  # the learner showed the skill
    "failure",  # the learner tried and missed
    "required_omission",  # a required element was left out: a failure the learner chose by omission
    "not_elicited",  # the exercise gave no chance to show the skill: says nothing
    "uninterpretable",  # the result cannot be read (technical error, unmatched free text)
    "stt_suspect",  # evidence that may come from a transcription error: weaker
    "interrupted",  # the attempt was cut short: says nothing
    "assisted",  # shown with help: counts as evidence but not as autonomous
]
ObservationSource = Literal["deterministic", "llm", "self_report", "placement"]
MasteryStatus = Literal["unknown", "weak", "developing", "mastered", "recheck", "uncertain"]
EdgeKind = Literal["prerequisite", "part_of"]

# Kinds that leave no trace in the estimate. A technical failure must never become a negative
# observation about the learner.
SILENT_KINDS: frozenset[str] = frozenset({"not_elicited", "uninterpretable", "interrupted"})
# Silent kinds that come from a technical error: they cannot replace an assessment either.
# `not_elicited` is a judgement ("this exercise did not test the skill"), so a reassessment of
# that kind withdraws the evidence it replaces: it is how a wrongly attributed result is retracted.
TECHNICAL_KINDS: frozenset[str] = frozenset({"uninterpretable", "interrupted"})
# Kinds that count against the learner on a critical skill.
FAILING_KINDS: frozenset[str] = frozenset({"failure", "required_omission"})


@dataclass(frozen=True, slots=True)
class Observation:
    """One piece of evidence about one skill. Append-only: a correction is a new observation.

    `observed_at` is when the learner produced the evidence (it drives forgetting);
    `assessed_at`, when a scorer judged it (it defaults to `observed_at`). Two observations
    that share (attempt_ref, producer, skill_id, item_ref, evidence_key) are the same evidence
    assessed twice: the one assessed last replaces the other, it does not add to it. A
    reassessment keeps the `observed_at` of the attempt and sets its own `assessed_at`; only
    two assessments made at the same instant are told apart by their id. A technical error
    (`uninterpretable`, `interrupted`) never replaces an assessment; a `not_elicited`
    reassessment withdraws the one it replaces and counts for nothing itself.
    `evidence["evidence_key"]` tells apart several observations of the same skill on the same
    item within one attempt and one producer (for example the rubric item behind each); an
    adapter that emits several such observations must give each its own key.
    `evidence["context"]` optionally names the circumstance (a case, a modality); without it,
    the item reference is the context.
    """

    id: str
    learner_id: str
    skill_id: str
    item_ref: str
    attempt_ref: str
    kind: ObservationKind
    outcome: float | None  # 0..1; None only for silent kinds
    reliability: float  # 0..1
    source: ObservationSource
    producer: str  # the exact scorer or prompt version that produced it
    evidence: Mapping[str, Any]
    observed_at: datetime
    assessed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at needs a timezone")
        if self.assessed_at is not None:
            if self.assessed_at.tzinfo is None:
                raise ValueError("assessed_at needs a timezone")
            if self.assessed_at < self.observed_at:
                raise ValueError("assessed_at cannot precede observed_at")
        if isinstance(self.reliability, bool) or not 0.0 <= self.reliability <= 1.0:
            raise ValueError("reliability lies in [0, 1]")
        if self.outcome is not None and (
            isinstance(self.outcome, bool) or not 0.0 <= self.outcome <= 1.0
        ):
            raise ValueError("outcome lies in [0, 1]")
        if self.outcome is None and self.kind not in SILENT_KINDS:
            raise ValueError(f"a '{self.kind}' observation needs an outcome")
        if self.outcome is not None:
            if self.kind in FAILING_KINDS and self.outcome > 0.5:
                raise ValueError(f"a '{self.kind}' observation has an outcome of at most 0.5")
            if self.kind == "success" and self.outcome < 0.5:
                raise ValueError("a 'success' observation has an outcome of at least 0.5")

    @property
    def assessed(self) -> datetime:
        return self.assessed_at or self.observed_at

    @property
    def evidence_key(self) -> str:
        return str(self.evidence.get("evidence_key", ""))

    @property
    def context(self) -> str:
        return str(self.evidence.get("context", self.item_ref))

    @property
    def replacement_key(self) -> tuple[str, str, str, str, str]:
        return (self.attempt_ref, self.producer, self.skill_id, self.item_ref, self.evidence_key)


@dataclass(frozen=True, slots=True)
class MasteryEstimate:
    """What the observations say about one skill now, with its uncertainty."""

    skill_id: str
    mean: float
    lower: float  # lower bound of the credible interval
    upper: float
    effective_n: float  # evidence still weighing after forgetting
    distinct_items: int
    distinct_contexts: int
    autonomous_n: float  # the part of effective_n that was shown without help
    last_observed_at: datetime | None
    status: MasteryStatus
    model_version: str


@dataclass(frozen=True, slots=True)
class Skill:
    id: str
    label: str
    family: str
    half_life_days: float  # evidence loses half its weight after this many days
    critical: bool = False  # a recent failure on it forbids calling it mastered

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a skill needs an id")
        if (
            isinstance(self.half_life_days, bool)
            or not isfinite(self.half_life_days)
            or self.half_life_days <= 0
        ):
            raise ValueError(f"{self.id}: half_life_days must be a finite positive number")


@dataclass(frozen=True, slots=True)
class SkillEdge:
    """`from_skill` is a prerequisite of, or a part of, `to_skill`, depending on the kind.

    For a prerequisite, `threshold` is the lower bound of mastery the prerequisite must reach
    before `to_skill` is considered ready.
    """

    from_skill: str
    to_skill: str
    kind: EdgeKind
    threshold: float = 0.5


@dataclass(frozen=True, slots=True)
class Candidate:
    """An exercise a policy may pick: what it trains or assesses, what it takes, what it costs."""

    item_ref: str
    skills: Mapping[str, float]  # targeted skills with their weight
    duration_minutes: float
    difficulty: float | None = None
    modality: str = ""
    expected_cost_micro_eur: int | None = None


@dataclass(frozen=True, slots=True)
class Constraints:
    available_minutes: float | None = None
    allowed_modalities: frozenset[str] | None = None
    recent_item_refs: frozenset[str] = frozenset()
    budget_micro_eur: int | None = None


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    item_ref: str
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Decision:
    """A selection and everything needed to explain and replay it."""

    chosen: ScoredCandidate | None
    alternatives: tuple[ScoredCandidate, ...]
    policy_version: str
    seed: int | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
