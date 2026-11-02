"""The file a generation run leaves: the exercises that passed every check, and those that did not.

A batch is read by a person before anything reaches the bank, and again by the import, which
re-runs every check the code can run. It carries the provenance of each stage (provider, model
asked and model that answered, prompt version and hash), so a bank row can always be traced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from ari.application.exercise_schemas import RUBRIC_VERSION
from ari.domain.models import utc_now

BATCH_SCHEMA: Final = "exercise-batch-v1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageInfo(_Model):
    """Which model ran a stage with which prompt; recorded from the calls themselves."""

    stage: Literal["author", "blind_solver", "rubric_judge"]
    provider: str
    model: str
    model_returned: str | None
    prompt_version: str
    prompt_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class SpecInfo(_Model):
    level: str
    category: str
    format: str
    setting_hint: str | None
    requested: int


class AcceptedEntry(_Model):
    exercise: dict[str, Any]  # An ExerciseCandidate; the import rebuilds a LanguageExercise.
    exercise_id: str
    solver: dict[str, Any]
    judge: dict[str, Any]


class RejectedEntry(_Model):
    candidate: dict[str, Any] | None
    stage: Literal["draft", "duplicate", "mechanical", "blind_solver", "rubric_judge", "error"]
    issues: list[str]
    detail: dict[str, Any] = Field(default_factory=dict)


class CallSummary(_Model):
    operation: str
    provider: str
    model: str
    status: str
    latency_ms: int
    usage: dict[str, Any]


class ExerciseBatch(_Model):
    schema_version: Literal["exercise-batch-v1"] = BATCH_SCHEMA
    batch_id: str
    created_at: datetime = Field(default_factory=utc_now)
    rubric_version: str = RUBRIC_VERSION
    mechanical_checks_version: str
    author: StageInfo
    solver: StageInfo
    judge: StageInfo
    specs: list[SpecInfo]
    accepted: list[AcceptedEntry]
    rejected: list[RejectedEntry]
    calls: list[CallSummary]
