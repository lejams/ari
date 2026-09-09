from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ari.application.services.telemetry import (
    ClockMark,
    aggregate_voice_metrics,
    duration_between,
    nearest_rank,
)
from ari.domain.models import (
    AudioDeliveryStatus,
    ClockDomain,
    InteractionMode,
    VoiceMetricTransport,
    VoiceTurnMetric,
    VoiceTurnMetricStatus,
)


def _metric(index: int, value: int | None, *, version: str = "1") -> VoiceTurnMetric:
    created = datetime(2026, 9, 3, tzinfo=UTC) + timedelta(seconds=index)
    return VoiceTurnMetric(
        id=f"metric-{version}-{index}",
        trace_id=f"trace-{version}-{index}",
        session_id=f"session-{index}",
        turn_id=f"turn-{index}",
        voice_stack_id="pipeline_economy",
        voice_stack_version=version,
        transport=VoiceMetricTransport.PIPELINE,
        models={"stt": "gpt-transcribe", "llm": "gpt-5.6-luna"},
        interaction_mode=InteractionMode.GUIDED,
        delivery_status=AudioDeliveryStatus.DELIVERED,
        speech_end_to_first_audio_sent_ms=value,
        clock_domains=(
            {"speech_end_to_first_audio_sent_ms": ClockDomain.SERVER}
            if value is not None
            else {}
        ),
        created_at=created,
        updated_at=created,
    )


def test_duration_rejects_incompatible_clock_domains() -> None:
    with pytest.raises(ValueError, match="across server and browser"):
        duration_between(
            ClockMark(ClockDomain.SERVER, 10),
            ClockMark(ClockDomain.BROWSER, 20),
        )


def test_nearest_rank_percentiles_are_deterministic() -> None:
    values = list(range(1, 21))

    assert nearest_rank(values, 0.50) == 10
    assert nearest_rank(values, 0.95) == 19
    assert nearest_rank([], 0.95) is None


def test_missing_metric_is_not_coerced_to_zero_and_sample_is_insufficient() -> None:
    report = aggregate_voice_metrics((_metric(1, None), _metric(2, 120)))
    stats = report["groups"][0]["metrics"]["speech_end_to_first_audio_sent_ms"]

    assert stats["total_turns"] == 2
    assert stats["available_values"] == 1
    assert stats["missing_values"] == 1
    assert stats["minimum"] == 120
    assert stats["status"] == "insufficient_data"


def test_rates_include_failed_and_unconfirmed_turns() -> None:
    delivered = _metric(1, 100)
    failed = replace(
        _metric(2, None),
        status=VoiceTurnMetricStatus.FAILED,
        delivery_status=AudioDeliveryStatus.UNCONFIRMED,
        error_count=1,
        interruption_count=1,
    )
    report = aggregate_voice_metrics((delivered, failed))
    stats = report["groups"][0]["metrics"]["turn_total_ms"]

    assert stats["error_rate"] == 0.5
    assert stats["confirmed_delivery_rate"] == 0.5
    assert stats["unconfirmed_delivery_rate"] == 0.5
    assert stats["interruption_rate"] == 0.5


def test_stack_versions_are_never_aggregated_together() -> None:
    report = aggregate_voice_metrics((_metric(1, 100, version="1"), _metric(2, 90, version="2")))

    assert [group["voice_stack_version"] for group in report["groups"]] == ["1", "2"]
