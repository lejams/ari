from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

from ari.domain.models import (
    AudioDeliveryStatus,
    ClockDomain,
    ConversationSession,
    ConversationTurn,
    ExecutionRecord,
    VoiceMetricTransport,
    VoiceTurnMetric,
    VoiceTurnMetricStatus,
    utc_now,
)

METRIC_SCHEMA_VERSION: Final = "voice-turn-metric-v2"
AGGREGATION_SCHEMA_VERSION: Final = "voice-metric-aggregation-v1"
MINIMUM_SLO_OBSERVATIONS: Final = 20
VOICE_DURATION_FIELDS: Final[tuple[str, ...]] = (
    "speech_end_to_transcript_final_ms",
    "transcript_final_to_llm_first_token_ms",
    "llm_total_ms",
    "llm_complete_to_tts_first_byte_ms",
    "tts_total_ms",
    "speech_end_to_first_audio_sent_ms",
    "speech_end_to_audio_started_ms",
    "audio_sent_to_playback_started_ms",
    "turn_total_ms",
)


@dataclass(frozen=True, slots=True)
class ClockMark:
    domain: ClockDomain
    monotonic_ms: float


def monotonic_mark(domain: ClockDomain = ClockDomain.SERVER) -> ClockMark:
    return ClockMark(domain=domain, monotonic_ms=time.perf_counter() * 1000.0)


def duration_between(start: ClockMark, end: ClockMark) -> int:
    if start.domain is not end.domain:
        raise ValueError(
            f"Cannot calculate a duration across {start.domain.value} and {end.domain.value} clocks"
        )
    if end.monotonic_ms < start.monotonic_ms:
        raise ValueError("Duration end precedes start")
    return int(end.monotonic_ms - start.monotonic_ms)


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def new_voice_metric(
    session: ConversationSession,
    turn: ConversationTurn,
    *,
    transport: VoiceMetricTransport,
    application_version: str | None,
    durations: Mapping[str, int | None],
    clock_domains: Mapping[str, ClockDomain],
    wall_timestamps_utc: Mapping[str, str],
    executions: Sequence[ExecutionRecord] = (),
    status: VoiceTurnMetricStatus = VoiceTurnMetricStatus.COMPLETED,
    error_count: int = 0,
) -> VoiceTurnMetric:
    unknown_fields = set(durations) - set(VOICE_DURATION_FIELDS)
    if unknown_fields:
        raise ValueError(f"Unknown voice duration fields: {sorted(unknown_fields)}")
    models = session.voice_stack_config.get("models", {})
    model_map = (
        {str(key): str(value) for key, value in models.items()}
        if isinstance(models, Mapping)
        else {}
    )
    provider_ids = {
        item.operation: item.provider_request_id
        for item in executions
        if item.provider_request_id is not None
    }
    if turn.provider_input_item_id is not None:
        provider_ids["input_item"] = turn.provider_input_item_id
    if turn.provider_response_id is not None:
        provider_ids["response"] = turn.provider_response_id
    prompt_versions = {
        item.operation: item.prompt_version
        for item in executions
        if item.prompt_version is not None
    }
    prompt_hashes = {
        item.operation: item.prompt_hash
        for item in executions
        if item.prompt_hash is not None
    }
    values: dict[str, object] = {name: durations.get(name) for name in VOICE_DURATION_FIELDS}
    return VoiceTurnMetric(
        id=turn.id,
        trace_id=turn.id,
        session_id=session.id,
        turn_id=turn.id,
        voice_stack_id=session.voice_stack_id,
        voice_stack_version=session.voice_stack_version,
        transport=transport,
        models=model_map,
        provider_ids=provider_ids,
        case_id=session.case_id,
        case_version=session.case_version,
        case_hash=session.case_hash,
        interaction_mode=session.interaction_mode,
        prompt_versions=prompt_versions,
        prompt_hashes=prompt_hashes,
        delivery_status=turn.delivery_status,
        application_version=application_version,
        clock_domains=clock_domains,
        wall_timestamps_utc=wall_timestamps_utc,
        interruption_count=0,
        error_count=error_count,
        retry_count=max(0, turn.audio_attempt - 1),
        status=status,
        **values,  # type: ignore[arg-type]
    )


def merge_voice_metric(
    metric: VoiceTurnMetric,
    *,
    delivery_status: AudioDeliveryStatus | None = None,
    durations: Mapping[str, int | None] | None = None,
    clock_domains: Mapping[str, ClockDomain] | None = None,
    wall_timestamps_utc: Mapping[str, str] | None = None,
    error_count_increment: int = 0,
    retry_count: int | None = None,
    status: VoiceTurnMetricStatus | None = None,
) -> VoiceTurnMetric:
    durations = durations or {}
    clock_domains = clock_domains or {}
    wall_timestamps_utc = wall_timestamps_utc or {}
    if set(durations) - set(VOICE_DURATION_FIELDS):
        raise ValueError("Cannot merge an unknown voice duration")
    values = {name: getattr(metric, name) for name in VOICE_DURATION_FIELDS}
    for name, value in durations.items():
        if value is not None:
            values[name] = value
    return replace(
        metric,
        delivery_status=delivery_status or metric.delivery_status,
        clock_domains={**metric.clock_domains, **clock_domains},
        wall_timestamps_utc={**metric.wall_timestamps_utc, **wall_timestamps_utc},
        error_count=metric.error_count + error_count_increment,
        retry_count=metric.retry_count if retry_count is None else retry_count,
        status=status or metric.status,
        updated_at=utc_now(),
        **values,
    )


def nearest_rank(values: Sequence[int | float], percentile: float) -> int | float | None:
    if not values:
        return None
    if not 0 < percentile <= 1:
        raise ValueError("Percentile must be in the interval (0, 1]")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def aggregate_voice_metrics(
    metrics: Iterable[VoiceTurnMetric],
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, object]:
    if window_start is not None and window_start.tzinfo is None:
        raise ValueError("window_start must be timezone-aware")
    if window_end is not None and window_end.tzinfo is None:
        raise ValueError("window_end must be timezone-aware")
    if window_start is not None and window_end is not None and window_end < window_start:
        raise ValueError("window_end precedes window_start")

    groups: dict[
        tuple[str, str, tuple[tuple[str, str], ...], str, str], list[VoiceTurnMetric]
    ] = defaultdict(list)
    for metric in metrics:
        if window_start is not None and metric.created_at < window_start:
            continue
        if window_end is not None and metric.created_at >= window_end:
            continue
        key = (
            metric.voice_stack_id,
            metric.voice_stack_version,
            tuple(sorted(metric.models.items())),
            metric.transport.value,
            metric.interaction_mode.value if metric.interaction_mode is not None else "unknown",
        )
        groups[key].append(metric)

    rendered_groups: list[dict[str, object]] = []
    for key in sorted(groups):
        stack_id, stack_version, models, transport, interaction_mode = key
        items = groups[key]
        total = len(items)
        errors = sum(
            item.status is VoiceTurnMetricStatus.FAILED or item.error_count > 0 for item in items
        )
        confirmed = sum(
            item.delivery_status is AudioDeliveryStatus.DELIVERED for item in items
        )
        unconfirmed = sum(
            item.delivery_status is AudioDeliveryStatus.UNCONFIRMED for item in items
        )
        interrupted = sum(item.interruption_count > 0 for item in items)
        statistics: dict[str, object] = {}
        for metric_name in VOICE_DURATION_FIELDS:
            available = [
                value
                for item in items
                if (value := getattr(item, metric_name)) is not None
            ]
            statistics[metric_name] = {
                "total_turns": total,
                "available_values": len(available),
                "missing_values": total - len(available),
                "p50": nearest_rank(available, 0.50),
                "p95": nearest_rank(available, 0.95),
                "minimum": min(available) if available else None,
                "maximum": max(available) if available else None,
                "error_rate": errors / total if total else None,
                "confirmed_delivery_rate": confirmed / total if total else None,
                "unconfirmed_delivery_rate": unconfirmed / total if total else None,
                "interruption_rate": interrupted / total if total else None,
                "status": (
                    "sufficient_data"
                    if len(available) >= MINIMUM_SLO_OBSERVATIONS
                    else "insufficient_data"
                ),
                "percentile_method": "nearest-rank: ceil(percentile * n), minimum rank 1",
            }
        rendered_groups.append(
            {
                "voice_stack_id": stack_id,
                "voice_stack_version": stack_version,
                "models": dict(models),
                "transport": transport,
                "interaction_mode": interaction_mode,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
                "metrics": statistics,
            }
        )
    return {
        "schema_version": AGGREGATION_SCHEMA_VERSION,
        "generated_at": utc_timestamp(),
        "groups": rendered_groups,
    }
