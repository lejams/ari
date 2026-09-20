"""Bundle drafts: training content generated from a gold protocol, before the registry.

A draft is the frozen result of one generation request: either a complete, validated
`ClinicalBundle` ready to import into the platform registry, or the list of reasons the
model's output could not be assembled. Drafts are never edited; a new request makes a new one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field

from ari.domain.clinical import ClinicalModel
from ari.domain.models import utc_now

Phase = Literal["arzt_patient", "arzt_arzt", "fachbegriffe"]
PersonaVariant = Literal["standard", "anxious", "talkative", "terse"]
PHASES: tuple[Phase, ...] = ("arzt_patient", "arzt_arzt", "fachbegriffe")


class BundleVariantRequest(ClinicalModel):
    """What the owner asks for: which phases, which patient temperament, which language level."""

    phases: tuple[Phase, ...] = Field(min_length=1)
    persona_variant: PersonaVariant = "standard"
    cefr: Literal["B1", "B2", "C1"] = "B2"
    # Bumped by the owner to regenerate; it becomes the case and scenario version.
    revision: Annotated[int, Field(ge=1, le=999)] = 1


class BundleDraftStatus(StrEnum):
    DRAFT = "draft"  # Validated bundle, waiting for the owner to import it.
    INVALID = "invalid"  # The model's output could not be assembled; see validation_errors.
    IMPORTED = "imported"  # In the platform registry as draft_unvalidated scenarios.


@dataclass(frozen=True, slots=True)
class ScenarioRef:
    id: str
    version: str
    phase: Phase


@dataclass(frozen=True, slots=True)
class BundleDraft:
    id: str
    gold_protocol_id: str
    gold_hash: str
    request: BundleVariantRequest
    status: BundleDraftStatus
    bundle: dict[str, Any] | None  # The ClinicalBundle as JSON; None when invalid.
    bundle_hash: str | None
    case_id: str | None
    case_version: str | None
    scenario_refs: tuple[ScenarioRef, ...] = ()
    validation_errors: tuple[str, ...] = ()
    ai_run_id: str | None = None
    created_by_account_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    imported_at: datetime | None = None
