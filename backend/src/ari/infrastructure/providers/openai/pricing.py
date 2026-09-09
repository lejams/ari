"""Versioned OpenAI-only pricing policy; application cost records remain neutral."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ari.application.services.costs import CostResult
from ari.domain.models import CostStatus

PRICING_VERSION: Final = "openai-2026-09-03"
PRICING_VERIFIED_ON: Final = "2026-09-03"

MODEL_SOURCE_URLS: Final[dict[str, str]] = {
    "gpt-5.6-luna": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
    "gpt-5.6-terra": "https://developers.openai.com/api/docs/models/compare",
    "gpt-realtime-2.1-mini": (
        "https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini"
    ),
    "gpt-realtime-2.1": "https://developers.openai.com/api/docs/models/gpt-realtime-2.1",
    "gpt-transcribe": "https://developers.openai.com/api/docs/models/gpt-transcribe",
    "gpt-live-transcribe": (
        "https://developers.openai.com/api/docs/models/gpt-live-transcribe"
    ),
    "gpt-4o-mini-tts": "https://developers.openai.com/api/docs/models/gpt-4o-mini-tts",
}


@dataclass(frozen=True, slots=True)
class TokenPrices:
    input_usd_per_million: float
    cached_input_usd_per_million: float
    output_usd_per_million: float
    cache_write_multiplier: float = 1.25


LLM_PRICES: Final[dict[str, TokenPrices]] = {
    "gpt-5.6-luna": TokenPrices(0.20, 0.02, 1.20),
    "gpt-5.6-terra": TokenPrices(2.00, 0.20, 12.00),
}

# text input, cached text input, audio input, cached audio input,
# text output, audio output; all prices are USD per million tokens.
REALTIME_PRICES: Final[dict[str, tuple[float, float, float, float, float, float]]] = {
    "gpt-realtime-2.1-mini": (0.60, 0.06, 10.00, 0.30, 2.40, 20.00),
    "gpt-realtime-2.1": (4.00, 0.40, 32.00, 0.40, 24.00, 64.00),
}

STT_USD_PER_MINUTE: Final[dict[str, float]] = {
    "gpt-transcribe": 0.0045,
    "gpt-live-transcribe": 0.017,
}

# Text input and audio output, in USD per million tokens.
TTS_PRICES: Final[dict[str, tuple[float, float]]] = {
    "gpt-4o-mini-tts": (0.60, 12.00),
}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _number(mapping: Mapping[str, object], *keys: str) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float) and math.isfinite(value) and value >= 0:
            return float(value)
    return None


def _unknown(component: str, model: str, reason: str) -> CostResult:
    source = MODEL_SOURCE_URLS.get(model)
    return CostResult(
        pricing_version=PRICING_VERSION,
        component=component,
        status=CostStatus.UNKNOWN,
        amount_usd=None,
        unknown_reason=reason,
        source_urls=(source,) if source else (),
    )


def calculate_llm_cost(model: str, usage: Mapping[str, object]) -> CostResult:
    price = LLM_PRICES.get(model)
    if price is None:
        return _unknown("llm", model, f"No versioned price for model {model}")
    input_tokens = _number(usage, "prompt_tokens", "input_tokens")
    output_tokens = _number(usage, "completion_tokens", "output_tokens")
    if input_tokens is None or output_tokens is None:
        return _unknown("llm", model, "Provider usage lacks input or output token totals")

    details = _mapping(
        usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    )
    cached_tokens = _number(details, "cached_tokens")
    if cached_tokens is None:
        cached_tokens = _number(usage, "cached_input_tokens")
    cache_write_tokens = _number(details, "cache_write_tokens")
    if cache_write_tokens is None:
        cache_write_tokens = _number(usage, "cache_write_tokens")

    assumptions: list[str] = []
    status = CostStatus.EXACT
    if cached_tokens is None:
        cached_tokens = 0.0
        status = CostStatus.ESTIMATED
        assumptions.append("cache usage absent; all reported input priced as uncached")
    if cache_write_tokens is None:
        cache_write_tokens = 0.0
    if cached_tokens + cache_write_tokens > input_tokens:
        return _unknown("llm", model, "Cached and cache-write tokens exceed input total")

    uncached_tokens = input_tokens - cached_tokens - cache_write_tokens
    amount = (
        uncached_tokens * price.input_usd_per_million
        + cached_tokens * price.cached_input_usd_per_million
        + cache_write_tokens
        * price.input_usd_per_million
        * price.cache_write_multiplier
        + output_tokens * price.output_usd_per_million
    ) / 1_000_000
    return CostResult(
        pricing_version=PRICING_VERSION,
        component="llm",
        status=status,
        amount_usd=amount,
        units={
            "input_uncached_tokens": uncached_tokens,
            "input_cached_tokens": cached_tokens,
            "input_cache_write_tokens": cache_write_tokens,
            "output_tokens": output_tokens,
        },
        assumptions=tuple(assumptions),
        source_urls=(MODEL_SOURCE_URLS[model],),
    )


def calculate_stt_cost(model: str, audio_seconds: float | None) -> CostResult:
    price = STT_USD_PER_MINUTE.get(model)
    if price is None:
        return _unknown("stt", model, f"No versioned duration price for model {model}")
    if audio_seconds is None:
        return _unknown("stt", model, "Reliable audio duration is unavailable")
    if not math.isfinite(audio_seconds) or audio_seconds < 0:
        raise ValueError("Audio duration must be finite and nonnegative")
    return CostResult(
        pricing_version=PRICING_VERSION,
        component="stt",
        status=CostStatus.EXACT,
        amount_usd=(audio_seconds / 60.0) * price,
        units={"audio_seconds": audio_seconds},
        assumptions=("duration derived only from provider VAD boundaries or PCM frames",),
        source_urls=(MODEL_SOURCE_URLS[model],),
    )


def calculate_tts_cost(model: str, usage: Mapping[str, object]) -> CostResult:
    price = TTS_PRICES.get(model)
    if price is None:
        return _unknown("tts", model, f"No versioned token price for model {model}")
    input_text_tokens = _number(usage, "input_text_tokens", "input_tokens")
    output_audio_tokens = _number(usage, "output_audio_tokens", "audio_output_tokens")
    known_amount = 0.0
    units: dict[str, float] = {}
    missing: list[str] = []
    if input_text_tokens is None:
        missing.append("input_text_tokens")
    else:
        units["input_text_tokens"] = input_text_tokens
        known_amount += input_text_tokens * price[0] / 1_000_000
    if output_audio_tokens is None:
        missing.append("output_audio_tokens")
    else:
        units["output_audio_tokens"] = output_audio_tokens
        known_amount += output_audio_tokens * price[1] / 1_000_000
    if len(missing) == 2:
        return _unknown(
            "tts",
            model,
            "Speech API did not return billable text or audio token units; "
            "PCM bytes are not converted",
        )
    return CostResult(
        pricing_version=PRICING_VERSION,
        component="tts",
        status=CostStatus.PARTIAL if missing else CostStatus.EXACT,
        amount_usd=known_amount,
        units=units,
        assumptions=(
            ("missing units were not inferred from characters or PCM bytes",)
            if missing
            else ()
        ),
        unknown_reason=(f"Missing billable units: {', '.join(missing)}" if missing else None),
        source_urls=(MODEL_SOURCE_URLS[model],),
    )


def calculate_realtime_cost(model: str, usage: Mapping[str, object]) -> CostResult:
    price = REALTIME_PRICES.get(model)
    if price is None:
        return _unknown("realtime", model, f"No versioned price for model {model}")
    inputs = _mapping(usage.get("input_token_details"))
    outputs = _mapping(usage.get("output_token_details"))
    input_text_total = _number(inputs, "text_tokens")
    input_audio_total = _number(inputs, "audio_tokens")
    output_text = _number(outputs, "text_tokens")
    output_audio = _number(outputs, "audio_tokens")
    if all(
        value is None
        for value in (input_text_total, input_audio_total, output_text, output_audio)
    ):
        return _unknown("realtime", model, "Provider usage has no modality-level token details")

    cached_details = _mapping(
        inputs.get("cached_tokens_details") or inputs.get("cached_token_details")
    )
    cached_text = _number(cached_details, "text_tokens")
    cached_audio = _number(cached_details, "audio_tokens")
    if cached_text is None:
        cached_text = _number(inputs, "cached_text_tokens")
    if cached_audio is None:
        cached_audio = _number(inputs, "cached_audio_tokens")
    cached_total = _number(inputs, "cached_tokens")

    required_modalities = (input_text_total, input_audio_total, output_text, output_audio)
    if any(value is None for value in required_modalities):
        return _partial_realtime_outputs(
            model, price, output_text, output_audio, "Missing text/audio modality totals"
        )
    assert input_text_total is not None
    assert input_audio_total is not None
    assert output_text is not None
    assert output_audio is not None

    status = CostStatus.EXACT
    assumptions: list[str] = []
    if cached_text is None or cached_audio is None:
        if (
            (cached_total is not None and cached_total > 0)
            or cached_text is not None
            or cached_audio is not None
        ):
            return _partial_realtime_outputs(
                model,
                price,
                output_text,
                output_audio,
                "Cached input total is not split between text and audio",
            )
        if cached_total == 0:
            cached_text = cached_audio = 0.0
        else:
            cached_text = cached_audio = 0.0
            status = CostStatus.ESTIMATED
            assumptions.append("cache usage absent; all reported input priced as uncached")
    if cached_text > input_text_total or cached_audio > input_audio_total:
        return _unknown("realtime", model, "Cached modality tokens exceed modality input totals")
    if cached_total is not None and cached_text + cached_audio != cached_total:
        return _unknown("realtime", model, "Cached modality tokens disagree with cached total")

    uncached_text = input_text_total - cached_text
    uncached_audio = input_audio_total - cached_audio
    amount = (
        uncached_text * price[0]
        + cached_text * price[1]
        + uncached_audio * price[2]
        + cached_audio * price[3]
        + output_text * price[4]
        + output_audio * price[5]
    ) / 1_000_000
    return CostResult(
        pricing_version=PRICING_VERSION,
        component="realtime",
        status=status,
        amount_usd=amount,
        units={
            "input_text_uncached_tokens": uncached_text,
            "input_text_cached_tokens": cached_text,
            "input_audio_uncached_tokens": uncached_audio,
            "input_audio_cached_tokens": cached_audio,
            "output_text_tokens": output_text,
            "output_audio_tokens": output_audio,
        },
        assumptions=tuple(assumptions),
        source_urls=(MODEL_SOURCE_URLS[model],),
    )


def _partial_realtime_outputs(
    model: str,
    price: tuple[float, float, float, float, float, float],
    output_text: float | None,
    output_audio: float | None,
    reason: str,
) -> CostResult:
    units: dict[str, float] = {}
    amount = 0.0
    if output_text is not None:
        units["output_text_tokens"] = output_text
        amount += output_text * price[4] / 1_000_000
    if output_audio is not None:
        units["output_audio_tokens"] = output_audio
        amount += output_audio * price[5] / 1_000_000
    if not units:
        return _unknown("realtime", model, reason)
    return CostResult(
        pricing_version=PRICING_VERSION,
        component="realtime",
        status=CostStatus.PARTIAL,
        amount_usd=amount,
        units=units,
        assumptions=("only unambiguous output modalities are included",),
        unknown_reason=reason,
        source_urls=(MODEL_SOURCE_URLS[model],),
    )


def estimate_llm_cost(model: str, usage: Mapping[str, object]) -> float | None:
    return calculate_llm_cost(model, usage).amount_usd


def estimate_stt_cost(model: str, audio_seconds: float) -> float | None:
    return calculate_stt_cost(model, audio_seconds).amount_usd


def estimate_realtime_cost(model: str, usage: Mapping[str, object]) -> float | None:
    return calculate_realtime_cost(model, usage).amount_usd


def estimate_tts_cost(model: str, usage: Mapping[str, object]) -> float | None:
    return calculate_tts_cost(model, usage).amount_usd


__all__ = [
    "PRICING_VERSION",
    "CostResult",
    "calculate_llm_cost",
    "calculate_realtime_cost",
    "calculate_stt_cost",
    "calculate_tts_cost",
    "estimate_llm_cost",
    "estimate_realtime_cost",
    "estimate_stt_cost",
    "estimate_tts_cost",
]
