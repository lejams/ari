"""Provider-neutral practice contracts.

This module deliberately contains no provider or persistence imports.  Structured
practice and patient simulation can therefore expose one lifecycle while keeping
their historical stores and wire contracts intact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from ari.domain.models import ExecutionRecord, utc_now


class PracticeHandleKind(StrEnum):
    STRUCTURED_TEXT = "structured_text"
    PATIENT_VOICE = "patient_voice"


class PracticeModality(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class PracticeTransport(StrEnum):
    NONE = "none"
    PIPELINE = "pipeline"
    REALTIME = "realtime"


# Short alias used by callers that prefer the name in the product brief.
PracticeKind = PracticeHandleKind


@dataclass(frozen=True, slots=True)
class NormalizedText:
    text: str
    version: str = "normalized-text-v1"

    @property
    def schema_version(self) -> str:
        return self.version


@dataclass(frozen=True, slots=True)
class CanonicalResponse:
    text: str
    version: str = "canonical-response-v1"
    selected_fact_ids: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    response_kind: str = "text"

    @property
    def schema_version(self) -> str:
        return self.version


@dataclass(frozen=True, slots=True)
class PracticeTurn:
    """Common turn projection shared by text, pipeline and Realtime paths."""

    id: str
    practice_id: str
    sequence: int
    simulation_id: str
    modality: PracticeModality
    raw_user_text: str
    normalized_user_text: NormalizedText | None = None
    canonical_response: CanonicalResponse | None = None
    context_fact_ids: tuple[str, ...] = ()
    selected_fact_ids: tuple[str, ...] = ()
    revealed_fact_ids: tuple[str, ...] = ()
    events: tuple[Mapping[str, Any], ...] = ()
    decisions: tuple[Mapping[str, Any], ...] = ()
    errors: tuple[Mapping[str, Any], ...] = ()
    executions: tuple[ExecutionRecord, ...] = ()
    cost_usd: float | None = None
    latency_ms: int | None = None
    observed_response_text: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    @property
    def user_text(self) -> str:
        return self.raw_user_text

    @property
    def simulation(self) -> str:
        """Backward-compatible name for the real scenario/case identifier."""
        return self.simulation_id


@dataclass(frozen=True, slots=True)
class PracticeFeedback:
    """Stable envelope; ``payload`` preserves the existing feedback schema."""

    state: str
    payload: Mapping[str, Any] | None = None
    version: str = "practice-feedback-v1"
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PracticeHandle:
    id: str
    learner_id: str
    kind: PracticeHandleKind
    modality: PracticeModality
    transport: PracticeTransport
    status: str
    request_id: str | None = None
    case_id: str | None = None
    case_version: str | None = None
    scenario_id: str | None = None
    scenario_version: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PracticeHandleKind(self.kind))
        object.__setattr__(self, "modality", PracticeModality(self.modality))
        object.__setattr__(self, "transport", PracticeTransport(self.transport))


@dataclass(frozen=True, slots=True)
class PracticeStateProjection:
    handle: PracticeHandle
    turns: tuple[PracticeTurn, ...] = ()
    feedback: PracticeFeedback | None = None
    status: str = "active"


# Public terminology used by early lifecycle design notes.
PracticeProjection = PracticeStateProjection
