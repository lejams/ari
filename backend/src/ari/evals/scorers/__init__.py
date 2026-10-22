"""Suites: how one dataset item is run through the real ARI service and scored.

Scorers are deterministic: they compare what the service returned with the annotation of
the item. None of them asks an LLM to judge (no suite needs a judge yet; a judge would come
with its own calibration suite).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from ari.application.contracts import LLMRequest, ProviderResult
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, MedicalCase
from ari.evals.metrics import Counts, Ratio

T = TypeVar("T", bound=BaseModel)


class EvalItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    case_id: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class CallRecord:
    execution: ExecutionRecord | None
    value: BaseModel | None  # The parsed output, when the provider returned one.


class RecordingProvider:
    """Wraps the configured provider and keeps every call of one run, failed ones included."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.calls: list[CallRecord] = []

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        try:
            result = await self._inner.generate_structured(request, response_model)
        except ProviderError as exc:
            execution = exc.execution if isinstance(exc.execution, ExecutionRecord) else None
            self.calls.append(CallRecord(execution, None))
            raise
        self.calls.append(CallRecord(result.execution, result.value))
        return result


@dataclass(frozen=True, slots=True)
class RunContext:
    prompts: Mapping[str, VersionedPrompt]
    feedback_language: str
    run_key: str  # Unique per item and repetition; becomes the session id of the calls.
    patient_engine: str = "training"  # "exam": the exam chain patient answers instead.


@dataclass(frozen=True, slots=True)
class ItemScore:
    verdict: bool
    outcomes: tuple[str, ...]  # Failure kinds, for example "fact_leak"; empty when it passes.
    counts: dict[str, float]
    signature: str  # What must stay identical between repetitions of the same item.
    detail: dict[str, Any] = field(default_factory=dict)  # Not written for the holdout.


@dataclass(frozen=True, slots=True)
class Reliability:
    """Measured precision of the observations one producer emits (replaces a prior)."""

    source: str
    producer: str
    precision: float | None
    observations: int


class Suite(Protocol):
    name: str
    scorer_version: str
    # Roles of `prompts` in the configuration this suite needs, for example ("patient",).
    prompt_roles: tuple[str, ...]
    output_schemas: tuple[type[BaseModel], ...]  # Structured outputs, hashed in the report.

    def parse_item(self, row: Mapping[str, object]) -> EvalItem: ...

    async def execute(
        self, item: EvalItem, case: MedicalCase, provider: LLMProvider, context: RunContext
    ) -> object: ...

    def score(
        self,
        item: EvalItem,
        case: MedicalCase,
        value: object | None,
        calls: Sequence[CallRecord],
    ) -> ItemScore: ...

    def ratios(self, keys: Iterable[str]) -> list[Ratio]: ...

    def reliability(self, counts: Counts, producers: Mapping[str, str]) -> list[Reliability]: ...
