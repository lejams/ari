"""Cost of one model call, from its recorded usage and a versioned price list.

Pure: no I/O, no clock. The price list is parsed by the infrastructure and handed in. A cost is
only ever computed from what the provider reported; a missing usage figure or a missing price
makes the cost unknown and says which component is missing, it is never counted as zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ari.domain.models import ExecutionRecord

Billing = Literal["tokens", "stt_minute", "tts_minute", "tts_character", "free"]

# Token components, in the order they are reported.
TOKEN_UNITS = (
    "input_token",
    "cached_input_token",
    "audio_input_token",
    "cached_audio_input_token",
    "output_token",
    "audio_output_token",
)
PRICE_UNITS = (*TOKEN_UNITS, "stt_minute", "tts_minute", "tts_character")


@dataclass(frozen=True, slots=True)
class ModelPrice:
    provider: str
    model: str
    billing: Billing
    currency: str
    # Price of ONE unit (one token, one minute, one character) in `currency`; None = unknown.
    unit_prices: Mapping[str, float | None]
    minimum_billed_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class PricingTable:
    version: str
    to_eur: Mapping[str, float]  # currency -> EUR rate
    models: Mapping[tuple[str, str], ModelPrice]


@dataclass(frozen=True, slots=True)
class CostResult:
    cost_micro_eur: int | None
    pricing_version: str | None
    unknown_components: tuple[str, ...] = ()


def cost_of(execution: ExecutionRecord, pricing: PricingTable | None) -> CostResult:
    return cost_of_usage(execution.provider, execution.model, execution.usage, pricing)


def cost_of_usage(
    provider: str, model: str, usage: Mapping[str, Any], pricing: PricingTable | None
) -> CostResult:
    if pricing is None:
        return CostResult(None, None, ("pricing",))
    price = pricing.models.get((provider, model))
    if price is None:
        return CostResult(None, pricing.version, (f"price:{provider}/{model}",))
    if price.billing == "free":
        return CostResult(0, pricing.version)
    rate = pricing.to_eur.get(price.currency)
    if rate is None:
        return CostResult(None, pricing.version, (f"currency:{price.currency}",))

    quantities, missing = _quantities(price, usage)
    unknown = list(missing)
    total = 0.0
    for unit, quantity in quantities.items():
        if quantity <= 0:
            continue
        unit_price = price.unit_prices.get(unit)
        if unit_price is None:
            unknown.append(unit)
            continue
        total += quantity * unit_price
    if unknown:
        return CostResult(None, pricing.version, tuple(unknown))
    return CostResult(round(total * rate * 1_000_000), pricing.version)


def _quantities(price: ModelPrice, usage: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    if price.billing == "tokens":
        return _tokens(usage)
    if price.billing == "tts_character":
        characters = _number(usage.get("characters"))
        if characters is None:
            return {}, ["usage.characters"]
        return {"tts_character": characters}, []
    seconds = _number(usage.get("audio_seconds"))
    if seconds is None:
        return {}, ["usage.audio_seconds"]
    if price.billing == "stt_minute":
        return {"stt_minute": max(seconds, price.minimum_billed_seconds) / 60}, []
    return {"tts_minute": seconds / 60}, []


def _tokens(usage: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    # Chat Completions: prompt_tokens / completion_tokens (reasoning tokens are part of the
    # completion and billed as output).
    if "prompt_tokens" in usage:
        prompt = _number(usage.get("prompt_tokens"))
        completion = _number(usage.get("completion_tokens"))
        if prompt is None or completion is None:
            return {}, ["usage.tokens"]
        cached = _number(_details(usage, "prompt_tokens_details").get("cached_tokens")) or 0.0
        return {
            "input_token": prompt - cached,
            "cached_input_token": cached,
            "output_token": completion,
        }, []
    # Realtime and Responses: input_tokens / output_tokens with text and audio details.
    if "input_tokens" in usage:
        inputs = _number(usage.get("input_tokens"))
        outputs = _number(usage.get("output_tokens"))
        if inputs is None or outputs is None:
            return {}, ["usage.tokens"]
        in_details = _details(usage, "input_token_details")
        out_details = _details(usage, "output_token_details")
        cached_details = _details(in_details, "cached_tokens_details")
        audio_in = _number(in_details.get("audio_tokens")) or 0.0
        cached_audio = _number(cached_details.get("audio_tokens")) or 0.0
        cached_total = _number(in_details.get("cached_tokens")) or 0.0
        cached_text = _number(cached_details.get("text_tokens"))
        if cached_text is None:
            cached_text = cached_total - cached_audio
        audio_out = _number(out_details.get("audio_tokens")) or 0.0
        return {
            "input_token": inputs - audio_in - cached_text,
            "cached_input_token": cached_text,
            "audio_input_token": audio_in - cached_audio,
            "cached_audio_input_token": cached_audio,
            "output_token": outputs - audio_out,
            "audio_output_token": audio_out,
        }, []
    return {}, ["usage.tokens"]


def _details(usage: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = usage.get(key)
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
