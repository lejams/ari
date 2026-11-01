"""LLM routing per task, the Claude adapter, and the Claude price list.

The Claude adapter runs the real Anthropic SDK against a mock HTTP transport: what is sent
(system prompt, schema, effort) and what comes back (validation, refusals, usage) are those of
the SDK, offline.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field

from ari.application.contracts import ExecutionContext, LLMRequest, ProviderResult
from ari.application.services.costing import cost_of, cost_of_usage
from ari.config import Settings
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionStatus
from ari.infrastructure.pricing import load_pricing
from ari.infrastructure.providers.anthropic import AnthropicLLMProvider
from ari.infrastructure.providers.anthropic.llm import split_messages
from ari.infrastructure.providers.llm_routing import (
    CONTENT_TASKS,
    LEARNER_TASKS,
    LLMRoute,
    RoutedLLMProvider,
    llm_provider_from_settings,
    routes_from_settings,
    task_of,
)

PRICING_DIR = Path(__file__).resolve().parents[1] / "src" / "ari" / "pricing"
V1 = load_pricing(PRICING_DIR / "pricing-v1.yaml")
V2 = load_pricing(PRICING_DIR / "pricing-v2.yaml")
OPERATIONS = (
    "patient_simulation",
    "placement_speaking",
    "session_evaluation",
    "fact_attribution",
    "protocol_segmentation",
    "protocol_extraction",
    "bundle_draft",
)


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    verdict: Literal["ok", "ko"]
    reason: Annotated[str, Field(min_length=3, max_length=50)]
    score: Annotated[int, Field(ge=0, le=5)]


def _request(operation: str = "session_evaluation") -> LLMRequest:
    return LLMRequest(
        messages=(
            {"role": "system", "content": "Bewerte das Gespräch."},
            {"role": "user", "content": "Transkript"},
        ),
        context=ExecutionContext(
            session_id="s1",
            operation=operation,
            case_version="1",
            case_hash="h",
            prompt_version="evaluation-v6",
            prompt_hash="ph",
        ),
    )


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, provider_mode="openai", openai_api_key="o", **kw)


# ----- routing --------------------------------------------------------------------------------


def test_each_operation_belongs_to_one_task() -> None:
    assert [task_of(op) for op in OPERATIONS] == [
        "patient", "patient", "evaluation", "fact_attribution", "content", "content", "content",
    ]  # fmt: skip


def test_default_routes_reproduce_the_previous_openai_routing() -> None:
    settings = _settings()
    routes = routes_from_settings(settings)
    assert {task: r.provider for task, r in routes.items()} == dict.fromkeys(
        ("patient", "evaluation", "fact_attribution", "content"), "openai"
    )
    assert routes["patient"] == LLMRoute("openai", "gpt-6-luna", settings.patient_timeout_seconds)
    assert routes["evaluation"].timeout_seconds == settings.evaluation_timeout_seconds
    assert routes["content"].timeout_seconds == settings.content_timeout_seconds
    # The fact audit has always run on the patient model and timeout.
    assert routes["fact_attribution"] == routes["patient"]


def test_the_fact_audit_follows_the_patient_unless_set() -> None:
    settings = _settings(
        patient_provider="anthropic", patient_model="claude-haiku-5-5", anthropic_api_key="a"
    )
    assert routes_from_settings(settings)["fact_attribution"].model == "claude-haiku-5-5"
    own = _settings(
        fact_attribution_provider="anthropic", fact_attribution_model="claude-sonnet-5-5"
    )
    route = routes_from_settings(own)["fact_attribution"]
    assert (route.provider, route.model) == ("anthropic", "claude-sonnet-5-5")


def test_compose_empty_strings_mean_unset() -> None:
    settings = _settings(
        fact_attribution_provider="", fact_attribution_model=" ", anthropic_effort=""
    )
    assert settings.fact_attribution_provider is None
    assert settings.fact_attribution_model is None and settings.anthropic_effort is None


class _Backend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, str, float]] = []

    async def generate(
        self, request: LLMRequest, response_model: type[Any], *, model: str, timeout_seconds: float
    ) -> ProviderResult[Any]:
        self.calls.append((request.context.operation, model, timeout_seconds))
        raise NotImplementedError


async def test_each_operation_is_sent_to_its_route() -> None:
    openai, anthropic = _Backend("openai"), _Backend("anthropic")
    provider = RoutedLLMProvider(
        {
            "patient": LLMRoute("openai", "gpt-6-luna", 30),
            "evaluation": LLMRoute("anthropic", "claude-sonnet-5-5", 60),
            "fact_attribution": LLMRoute("anthropic", "claude-sonnet-5-5", 30),
            "content": LLMRoute("openai", "gpt-6-luna", 300),
        },
        {"openai": openai, "anthropic": anthropic},
    )
    for operation in OPERATIONS:
        with pytest.raises(NotImplementedError):
            await provider.generate_structured(_request(operation), _Answer)
    assert [c[0] for c in anthropic.calls] == ["session_evaluation", "fact_attribution"]
    assert anthropic.calls[0][1:] == ("claude-sonnet-5-5", 60)
    assert openai.calls[0] == ("patient_simulation", "gpt-6-luna", 30)
    assert openai.calls[-1] == ("bundle_draft", "gpt-6-luna", 300)


async def test_an_operation_of_a_task_not_run_here_is_refused() -> None:
    provider = RoutedLLMProvider(
        {"content": LLMRoute("openai", "gpt-6-luna", 300)}, {"openai": _Backend("openai")}
    )
    with pytest.raises(ValueError, match="not routed"):
        await provider.generate_structured(_request("session_evaluation"), _Answer)


def test_a_claude_route_needs_its_key_and_a_price() -> None:
    claude = {"evaluation_provider": "anthropic", "evaluation_model": "claude-sonnet-5-5"}
    with pytest.raises(RuntimeError, match="ARI_ANTHROPIC_API_KEY"):
        llm_provider_from_settings(_settings(**claude), V2, LEARNER_TASKS)
    with pytest.raises(RuntimeError, match="pricing-v2"):
        llm_provider_from_settings(_settings(anthropic_api_key="a", **claude), V1, LEARNER_TASKS)
    provider = llm_provider_from_settings(
        _settings(anthropic_api_key="a", **claude), V2, LEARNER_TASKS
    )
    assert provider.route("session_evaluation") == LLMRoute("anthropic", "claude-sonnet-5-5", 60.0)
    assert provider.route("patient_simulation").provider == "openai"


def test_the_content_pipeline_on_claude_needs_no_openai_key() -> None:
    settings = Settings(
        _env_file=None,
        provider_mode="openai",
        content_provider="anthropic",
        content_model="claude-sonnet-5-5",
        anthropic_api_key="a",
    )
    provider = llm_provider_from_settings(settings, V2, CONTENT_TASKS)
    assert provider.route("protocol_extraction").provider == "anthropic"


def test_default_settings_build_an_openai_only_router() -> None:
    provider = llm_provider_from_settings(_settings(), V2, LEARNER_TASKS)
    assert {provider.route(op).provider for op in OPERATIONS[:4]} == {"openai"}


# ----- sampling settings of the OpenAI evaluation -------------------------------------------


class _Completions:
    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.seen.append(kwargs)
        raise RuntimeError("stop here")


def _openai_with(completions: _Completions, **kw: Any) -> Any:
    from types import SimpleNamespace

    from ari.infrastructure.providers.openai import OpenAILLMProvider

    provider = OpenAILLMProvider(
        "k",
        patient_model="p",
        evaluation_model="e",
        patient_timeout_seconds=5,
        evaluation_timeout_seconds=5,
        **kw,
    )
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))  # type: ignore[assignment]
    return provider


async def test_the_evaluation_alone_gets_the_sampling_settings() -> None:
    completions = _Completions()
    provider = _openai_with(completions, evaluation_reasoning_effort="high", evaluation_seed=7)
    for operation in ("session_evaluation", "patient_simulation"):
        with pytest.raises(ProviderError):
            await provider.generate_structured(_request(operation), _Answer)
    evaluation, patient = completions.seen
    assert evaluation["reasoning_effort"] == "high" and evaluation["seed"] == 7
    assert "reasoning_effort" not in patient and "seed" not in patient


async def test_each_pass_of_the_evaluation_shifts_the_seed() -> None:
    completions = _Completions()
    provider = _openai_with(completions, evaluation_seed=7)
    for sample in (0, 1, 2):
        with pytest.raises(ProviderError):
            await provider.generate_structured(
                replace(_request("session_evaluation"), sample=sample), _Answer
            )
    assert [seen["seed"] for seen in completions.seen] == [7, 8, 9]
    # The configured seed itself is not changed by the shifts.
    assert provider._evaluation_options["seed"] == 7


async def test_without_sampling_settings_the_models_defaults_are_kept() -> None:
    completions = _Completions()
    with pytest.raises(ProviderError):
        await _openai_with(completions).generate_structured(_request(), _Answer)
    assert "reasoning_effort" not in completions.seen[0] and "seed" not in completions.seen[0]


def test_compose_empty_strings_leave_the_sampling_settings_unset() -> None:
    settings = _settings(evaluation_reasoning_effort="", evaluation_seed="")
    assert settings.evaluation_reasoning_effort is None and settings.evaluation_seed is None
    assert _settings(evaluation_seed="7").evaluation_seed == 7


# ----- the Claude adapter, real SDK, mock transport -------------------------------------------


def _claude(respond: dict[str, Any], seen: list[dict[str, Any]], **kw: Any) -> AnthropicLLMProvider:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=respond)

    client = AsyncAnthropic(
        api_key="test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicLLMProvider("test", client=client, **kw)


def _message(text: str, *, stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        **extra,
    }


async def test_claude_returns_the_validated_schema_and_a_costed_execution() -> None:
    seen: list[dict[str, Any]] = []
    answer = {"verdict": "ok", "reason": "Anamnese vollständig", "score": 4}
    provider = _claude(_message(json.dumps(answer)), seen, effort="medium")
    result = await provider.generate(
        _request(), _Answer, model="claude-sonnet-5-5", timeout_seconds=5
    )

    assert result.value == _Answer(**answer)
    sent = seen[0]
    assert sent["model"] == "claude-sonnet-5-5" and sent["max_tokens"] == 16000
    assert sent["system"] == "Bewerte das Gespräch."
    assert sent["messages"] == [{"role": "user", "content": "Transkript"}]
    assert sent["output_config"]["effort"] == "medium"
    schema = sent["output_config"]["format"]["schema"]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert set(schema["properties"]) == {"verdict", "reason", "score"}
    execution = result.execution
    assert (execution.provider, execution.model, execution.status) == (
        "anthropic", "claude-sonnet-5-5", ExecutionStatus.SUCCEEDED,
    )  # fmt: skip
    assert execution.provider_request_id == "msg_test"
    assert execution.model_returned == "claude-sonnet-5-5"
    assert (execution.prompt_version, execution.operation) == (
        "evaluation-v6",
        "session_evaluation",
    )
    # 1 000 input x 2 $ + 200 output x 10 $ per million = 0.004 $ x 0.88 = 3 520 micro-euros.
    cost = cost_of(execution, V2)
    assert cost.unknown_components == () and cost.cost_micro_eur == 3520
    assert cost.pricing_version == "pricing-v2"


async def test_without_effort_the_model_default_is_kept() -> None:
    seen: list[dict[str, Any]] = []
    answer = {"verdict": "ko", "reason": "Allergien fehlen", "score": 1}
    provider = _claude(_message(json.dumps(answer)), seen)
    await provider.generate(_request(), _Answer, model="claude-sonnet-5-5", timeout_seconds=5)
    assert "effort" not in seen[0]["output_config"]


async def test_an_answer_breaking_the_schema_constraints_is_a_provider_failure() -> None:
    seen: list[dict[str, Any]] = []
    bad = {"verdict": "ok", "reason": "x", "score": 9}  # too short, out of range
    provider = _claude(_message(json.dumps(bad)), seen)
    with pytest.raises(ProviderError) as caught:
        await provider.generate(_request(), _Answer, model="claude-sonnet-5-5", timeout_seconds=5)
    assert caught.value.execution.status is ExecutionStatus.FAILED


async def test_a_refusal_is_a_traceable_provider_failure_never_a_result() -> None:
    seen: list[dict[str, Any]] = []
    refusal = _message(
        "", stop_reason="refusal",
        stop_details={"type": "refusal", "category": "bio", "explanation": "declined"},
    )  # fmt: skip
    provider = _claude(refusal, seen)
    with pytest.raises(ProviderError) as caught:
        await provider.generate(_request(), _Answer, model="claude-sonnet-5-5", timeout_seconds=5)
    execution = caught.value.execution
    assert execution.provider == "anthropic" and execution.status is ExecutionStatus.FAILED
    assert execution.error_code is not None


async def test_a_response_that_parses_but_did_not_end_normally_is_refused() -> None:
    seen: list[dict[str, Any]] = []
    answer = {"verdict": "ok", "reason": "Anamnese vollständig", "score": 4}
    refusal = _message(
        json.dumps(answer), stop_reason="refusal",
        stop_details={"type": "refusal", "category": "bio", "explanation": "declined"},
    )  # fmt: skip
    provider = _claude(refusal, seen)
    with pytest.raises(ProviderError) as caught:
        await provider.generate(_request(), _Answer, model="claude-sonnet-5-5", timeout_seconds=5)
    message = caught.value.execution.error_message or ""
    assert "stop_reason=refusal" in message and "bio" in message


async def test_a_truncated_answer_is_a_provider_failure() -> None:
    seen: list[dict[str, Any]] = []
    provider = _claude(_message('{"verdict": "ok", "rea', stop_reason="max_tokens"), seen)
    with pytest.raises(ProviderError, match="Anthropic"):
        await provider.generate(_request(), _Answer, model="claude-sonnet-5-5", timeout_seconds=5)


def test_system_messages_move_to_the_system_parameter() -> None:
    system, turns = split_messages(
        (
            {"role": "system", "content": "A"},
            {"role": "user", "content": "B"},
            {"role": "user", "content": "C"},
            {"role": "assistant", "content": "D"},
        )
    )
    assert system == "A"
    assert turns == [{"role": "user", "content": "B\n\nC"}, {"role": "assistant", "content": "D"}]
    assert split_messages(({"role": "user", "content": "B"},))[0] is None


# ----- prices -------------------------------------------------------------------------------


def test_pricing_v2_keeps_every_v1_price_and_adds_claude() -> None:
    assert V2.version == "pricing-v2" and V2.to_eur == V1.to_eur
    for key, price in V1.models.items():
        assert V2.models[key] == price, key
    added = set(V2.models) - set(V1.models)
    assert added == {
        ("anthropic", "claude-sonnet-5-5"),
        ("anthropic", "claude-opus-5-5"),
        ("anthropic", "claude-haiku-5-5"),
    }


def test_claude_usage_counts_cache_reads_and_never_hides_cache_writes() -> None:
    read = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 1_000_000}
    assert cost_of_usage("anthropic", "claude-sonnet-5-5", read, V2).cost_micro_eur == 176_000
    written = {"input_tokens": 10, "output_tokens": 10, "cache_creation_input_tokens": 500}
    unknown = cost_of_usage("anthropic", "claude-sonnet-5-5", written, V2)
    assert unknown.cost_micro_eur is None
    assert unknown.unknown_components == ("cache_write_input_token",)
    # Null cache fields, as the SDK reports them, count as no cache.
    plain = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": None,
             "cache_creation_input_tokens": None}  # fmt: skip
    assert cost_of_usage("anthropic", "claude-sonnet-5-5", plain, V2).cost_micro_eur == 1_760_000
