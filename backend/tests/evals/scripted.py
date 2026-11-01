"""Scripted providers for the evaluation tests: no network, outputs chosen by the test."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from ari.application.contracts import LLMRequest, ProviderResult
from ari.config import PROJECT_ROOT
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id
from ari.evals.configs import EvalConfig, load_prompts
from ari.evals.manifest import Dataset, load_dataset
from ari.evals.runner import RunRequest
from ari.evals.scorers import Suite
from ari.infrastructure.pricing import load_pricing

T = TypeVar("T", bound=BaseModel)
DATASETS = PROJECT_ROOT / "evals" / "datasets"
GATES = PROJECT_ROOT / "evals" / "gates.yaml"
PROMPTS = PROJECT_ROOT / "backend" / "src" / "ari" / "prompts"
PRICING = load_pricing(PROJECT_ROOT / "backend" / "src" / "ari" / "pricing" / "pricing-v2.yaml")
# Chat Completions usage of one call: 1 000 tokens in, 200 out.
USAGE = {"prompt_tokens": 1000, "completion_tokens": 200}

Respond = Callable[[LLMRequest, dict[str, Any]], dict[str, Any]]


class ScriptedProvider:
    """Answers with `respond(request, payload)`, recorded as `provider/model` with `usage`."""

    def __init__(
        self,
        respond: Respond,
        *,
        provider: str = "openai",
        model: str = "gpt-6-luna",
        usage: dict[str, Any] | None = None,
    ) -> None:
        self._respond = respond
        self._provider = provider
        self._model = model
        self._usage = USAGE if usage is None else usage
        self.calls = 0

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        self.calls += 1
        payload = json.loads(request.messages[-1]["content"])
        value = response_model.model_validate_json(json.dumps(self._respond(request, payload)))
        context = request.context
        return ProviderResult(
            value=value,
            execution=ExecutionRecord(
                id=new_id(),
                session_id=context.session_id,
                operation=context.operation,
                provider=self._provider,
                model=self._model,
                model_returned=f"{self._model}-2026-10-01",
                status=ExecutionStatus.SUCCEEDED,
                prompt_version=context.prompt_version,
                prompt_hash=context.prompt_hash,
                case_version=context.case_version,
                case_hash=context.case_hash,
                latency_ms=7,
                usage=dict(self._usage),
                turn_id=context.turn_id,
            ),
        )


def item_id_of(request: LLMRequest) -> str:
    """The runner names each run `evals:<suite>:<item id>:r<repetition>`."""
    return request.context.session_id.split(":")[2]


def repetition_of(request: LLMRequest) -> int:
    return int(request.context.session_id.rsplit(":r", 1)[1])


def config(
    config_id: str, model: str | None = None, provider: str = "openai", engine: str = "training"
) -> EvalConfig:
    """A live configuration routing both tasks to `provider:model`, or the fake one."""
    route = {"provider": provider, "model": model}
    return EvalConfig.model_validate(
        {
            "id": config_id,
            "description": f"test {config_id}",
            "provider": "live" if model else "fake",
            "routes": {"patient": route, "evaluation": route} if model else {},
            "prompts": {
                "patient": "patient-exam-v1" if engine == "exam" else "patient-v3",
                "evaluation": "evaluation-v10",
            },
            "patient_engine": engine,
        }
    )


def request_for(
    suite: Suite,
    provider: Any,
    *,
    dataset: Dataset | None = None,
    config_id: str = "scripted",
    split: str = "dev",
    gate_mode: bool = False,
    repetitions: int = 1,
    engine: str = "training",
    **extra: Any,
) -> RunRequest:
    name = suite.name
    the_config = config(config_id, "gpt-6-luna", engine=engine)
    return RunRequest(
        suite=suite,
        dataset=dataset or load_dataset(DATASETS, name, "1"),
        split=split,  # type: ignore[arg-type]
        gate_mode=gate_mode,
        config=the_config,
        config_sha256="0" * 64,
        provider=provider,
        prompts=load_prompts(the_config, suite.prompt_roles, PROMPTS),
        pricing=PRICING,
        repetitions=repetitions,
        **extra,
    )
