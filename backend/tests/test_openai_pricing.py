from __future__ import annotations

from dataclasses import replace

import pytest

from ari.application.services.costs import summarize_session_cost
from ari.domain.models import CostStatus, ExecutionRecord, ExecutionStatus
from ari.infrastructure.providers.openai.pricing import (
    PRICING_VERSION,
    calculate_llm_cost,
    calculate_realtime_cost,
    calculate_stt_cost,
    calculate_tts_cost,
)


def test_realtime_cost_without_cache() -> None:
    result = calculate_realtime_cost(
        "gpt-realtime-2.1-mini",
        {
            "input_token_details": {
                "text_tokens": 1_000,
                "audio_tokens": 2_000,
                "cached_tokens": 0,
            },
            "output_token_details": {"text_tokens": 3_000, "audio_tokens": 4_000},
        },
    )

    assert PRICING_VERSION == "openai-2026-09-03"
    assert result.status is CostStatus.EXACT
    assert result.amount_usd == pytest.approx(0.1078)


def test_realtime_cost_with_split_text_and_audio_cache() -> None:
    result = calculate_realtime_cost(
        "gpt-realtime-2.1-mini",
        {
            "input_token_details": {
                "text_tokens": 6_000,
                "audio_tokens": 4_000,
                "cached_tokens": 8_000,
                "cached_tokens_details": {"text_tokens": 5_000, "audio_tokens": 3_000},
            },
            "output_token_details": {"text_tokens": 1_000, "audio_tokens": 2_000},
        },
    )

    assert result.status is CostStatus.EXACT
    assert result.units["input_text_uncached_tokens"] == 1_000
    assert result.units["input_audio_cached_tokens"] == 3_000
    assert result.amount_usd == pytest.approx(0.0542)


def test_realtime_ambiguous_cache_is_partial_and_never_double_billed() -> None:
    result = calculate_realtime_cost(
        "gpt-realtime-2.1",
        {
            "input_token_details": {
                "text_tokens": 6_000,
                "audio_tokens": 4_000,
                "cached_tokens": 8_000,
            },
            "output_token_details": {"text_tokens": 1_000, "audio_tokens": 2_000},
        },
    )

    assert result.status is CostStatus.PARTIAL
    assert result.amount_usd == pytest.approx(0.152)
    assert set(result.units) == {"output_text_tokens", "output_audio_tokens"}
    assert "not split" in (result.unknown_reason or "")


def test_missing_usage_is_unknown_not_zero() -> None:
    result = calculate_realtime_cost("gpt-realtime-2.1", {})

    assert result.status is CostStatus.UNKNOWN
    assert result.amount_usd is None


def test_llm_cost_separates_cached_input() -> None:
    result = calculate_llm_cost(
        "gpt-5.6-luna",
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1_000_000,
            "prompt_tokens_details": {"cached_tokens": 250_000},
        },
    )

    assert result.status is CostStatus.EXACT
    assert result.amount_usd == pytest.approx(1.355)
    assert result.units["input_uncached_tokens"] == 750_000


def test_stt_uses_exact_audio_duration() -> None:
    result = calculate_stt_cost("gpt-transcribe", 60.0)

    assert result.status is CostStatus.EXACT
    assert result.amount_usd == pytest.approx(0.0045)
    assert result.units == {"audio_seconds": 60.0}


def test_tts_without_billable_token_units_is_unknown() -> None:
    result = calculate_tts_cost(
        "gpt-4o-mini-tts", {"characters": 400, "pcm_bytes": 48_000}
    )

    assert result.status is CostStatus.UNKNOWN
    assert result.amount_usd is None
    assert "PCM bytes" in (result.unknown_reason or "")


def test_tts_with_provider_billable_units_is_exact() -> None:
    result = calculate_tts_cost(
        "gpt-4o-mini-tts",
        {"input_text_tokens": 1_000, "output_audio_tokens": 2_000},
    )

    assert result.status is CostStatus.EXACT
    assert result.amount_usd == pytest.approx(0.0246)


def _execution(operation: str, cost_status: CostStatus, amount: float | None) -> ExecutionRecord:
    return ExecutionRecord(
        id=operation,
        session_id="session",
        operation=operation,
        provider="openai",
        model="model",
        status=ExecutionStatus.SUCCEEDED,
        prompt_version=None,
        prompt_hash=None,
        case_version="1",
        case_hash="hash",
        latency_ms=1,
        usage={},
        cost_status=cost_status,
        cost_amount_usd=amount,
        cost_unknown_reason="missing" if cost_status is CostStatus.UNKNOWN else None,
    )


def test_session_cost_with_unknown_component_is_partial() -> None:
    exact = _execution("patient_simulation", CostStatus.EXACT, 0.01)
    missing = _execution("text_to_speech", CostStatus.UNKNOWN, None)
    summary = summarize_session_cost("session", "pipeline_economy", (exact, missing))

    assert summary["status"] == "partial"
    assert summary["exact_cost_usd"] == 0.01
    assert summary["total_is_exact"] is False
    assert summary["missing_operations"] == ["text_to_speech"]
    assert summary["components"]["patient_llm"]["included_operations"] == [
        "patient_simulation"
    ]


def test_session_without_executions_has_unknown_cost_not_exact_zero() -> None:
    summary = summarize_session_cost("session", "pipeline_economy", ())

    assert summary["status"] == "unknown"
    assert summary["total_is_exact"] is False
    assert summary["exact_cost_usd"] is None
    assert summary["estimated_cost_usd"] is None
    assert summary["partial_cost_usd"] is None
    assert summary["included_operations"] == []


def test_zero_value_estimate_remains_estimated() -> None:
    estimated = _execution("patient_response", CostStatus.ESTIMATED, 0.0)

    summary = summarize_session_cost("session", "pipeline_economy", (estimated,))

    assert summary["status"] == "estimated"
    assert summary["estimated_cost_usd"] == 0.0


def test_legacy_estimate_is_explicitly_estimated() -> None:
    execution = replace(
        _execution("patient_response", CostStatus.UNKNOWN, None),
        estimated_cost_usd=0.2,
    )

    assert execution.cost_status is CostStatus.ESTIMATED
    assert execution.cost_amount_usd == 0.2


@pytest.mark.parametrize("status", [CostStatus.EXACT, CostStatus.ESTIMATED, CostStatus.PARTIAL])
def test_known_execution_cost_requires_an_amount(status: CostStatus) -> None:
    with pytest.raises(ValueError, match="requires an amount"):
        _execution("patient_simulation", status, None)
