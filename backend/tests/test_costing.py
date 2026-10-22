"""Costs of model calls: computed from reported usage and a versioned price list, never guessed."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ari.application.services.costing import (
    CostResult,
    ModelPrice,
    PricingTable,
    cost_of,
    cost_of_usage,
)
from ari.config import Settings
from ari.domain.models import ExecutionRecord, ExecutionStatus
from ari.infrastructure.pricing import load_pricing

PER_MILLION = 1 / 1_000_000


def _table(*prices: ModelPrice) -> PricingTable:
    return PricingTable(
        version="pricing-test",
        to_eur={"USD": 0.5, "EUR": 1.0},
        models={(p.provider, p.model): p for p in prices},
    )


LLM = ModelPrice(
    "openai",
    "llm",
    "tokens",
    "USD",
    {
        "input_token": 2 * PER_MILLION,
        "cached_input_token": 0.2 * PER_MILLION,
        "output_token": 12 * PER_MILLION,
    },
)
LLM_NO_CACHE_PRICE = ModelPrice(
    "openai",
    "llm-nocache",
    "tokens",
    "USD",
    {"input_token": 2 * PER_MILLION, "cached_input_token": None, "output_token": 12 * PER_MILLION},
)
REALTIME = ModelPrice(
    "openai",
    "realtime",
    "tokens",
    "USD",
    {
        "input_token": 0.6 * PER_MILLION,
        "cached_input_token": 0.06 * PER_MILLION,
        "audio_input_token": 10 * PER_MILLION,
        "cached_audio_input_token": 0.3 * PER_MILLION,
        "output_token": 2.4 * PER_MILLION,
        "audio_output_token": 20 * PER_MILLION,
    },
)
STT = ModelPrice("stt.example", "whisper", "stt_minute", "USD", {"stt_minute": 0.006}, 10.0)
TTS_MINUTE = ModelPrice("openai", "tts", "tts_minute", "USD", {"tts_minute": 0.015})
TTS_CHARACTER = ModelPrice(
    "elevenlabs", "voice", "tts_character", "USD", {"tts_character": 0.00004}
)
FREE = ModelPrice("fake", "deterministic-fake-v1", "free", "USD", {})
TABLE = _table(LLM, LLM_NO_CACHE_PRICE, REALTIME, STT, TTS_MINUTE, TTS_CHARACTER, FREE)


def _execution(provider: str, model: str, usage: dict[str, object]) -> ExecutionRecord:
    return ExecutionRecord(
        id="e",
        session_id="s",
        operation="op",
        provider=provider,
        model=model,
        status=ExecutionStatus.SUCCEEDED,
        prompt_version=None,
        prompt_hash=None,
        case_version="1",
        case_hash="h",
        latency_ms=1,
        usage=usage,
    )


def test_chat_completion_separates_cached_from_fresh_input() -> None:
    usage = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 100_000,
        "prompt_tokens_details": {"cached_tokens": 400_000},
    }
    # 600k fresh x 2 + 400k cached x 0.2 + 100k out x 12 = 1.2 + 0.08 + 1.2 = 2.48 USD
    result = cost_of(_execution("openai", "llm", usage), TABLE)
    assert result == CostResult(round(2.48 * 0.5 * 1_000_000), "pricing-test")


def test_chat_completion_without_cache_ignores_unknown_cache_price() -> None:
    usage = {"prompt_tokens": 1_000, "completion_tokens": 10, "prompt_tokens_details": None}
    result = cost_of_usage("openai", "llm-nocache", usage, TABLE)
    assert result.cost_micro_eur == round((1_000 * 2 + 10 * 12) * 0.5)
    assert result.unknown_components == ()


def test_cached_tokens_without_a_cached_price_make_the_cost_unknown() -> None:
    usage = {
        "prompt_tokens": 1_000,
        "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 500},
    }
    result = cost_of_usage("openai", "llm-nocache", usage, TABLE)
    assert result == CostResult(None, "pricing-test", ("cached_input_token",))


def test_realtime_usage_prices_text_and_audio_separately() -> None:
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 500_000,
        "input_token_details": {
            "text_tokens": 200_000,
            "audio_tokens": 800_000,
            "cached_tokens": 300_000,
            "cached_tokens_details": {"text_tokens": 100_000, "audio_tokens": 200_000},
        },
        "output_token_details": {"text_tokens": 100_000, "audio_tokens": 400_000},
    }
    expected_usd = (
        100_000 * 0.6  # fresh text in
        + 100_000 * 0.06  # cached text in
        + 600_000 * 10  # fresh audio in
        + 200_000 * 0.3  # cached audio in
        + 100_000 * 2.4  # text out
        + 400_000 * 20  # audio out
    ) * PER_MILLION
    result = cost_of_usage("openai", "realtime", usage, TABLE)
    assert result.cost_micro_eur == round(expected_usd * 0.5 * 1_000_000)


def test_realtime_usage_without_details_counts_text_tokens() -> None:
    result = cost_of_usage("openai", "realtime", {"input_tokens": 12, "output_tokens": 5}, TABLE)
    assert result.cost_micro_eur == round((12 * 0.6 + 5 * 2.4) * 0.5)


def test_speech_to_text_applies_the_minimum_billed_duration() -> None:
    short = cost_of_usage("stt.example", "whisper", {"audio_seconds": 3.0}, TABLE)
    ten = cost_of_usage("stt.example", "whisper", {"audio_seconds": 10.0}, TABLE)
    minute = cost_of_usage("stt.example", "whisper", {"audio_seconds": 60.0}, TABLE)
    assert short.cost_micro_eur == ten.cost_micro_eur == round(10 / 60 * 0.006 * 0.5 * 1_000_000)
    assert minute.cost_micro_eur == round(0.006 * 0.5 * 1_000_000)


def test_text_to_speech_by_minute_and_by_character() -> None:
    minute = cost_of_usage("openai", "tts", {"characters": 900, "audio_seconds": 120.0}, TABLE)
    assert minute.cost_micro_eur == round(2 * 0.015 * 0.5 * 1_000_000)
    character = cost_of_usage("elevenlabs", "voice", {"characters": 1_000}, TABLE)
    assert character.cost_micro_eur == round(1_000 * 0.00004 * 0.5 * 1_000_000)


def test_fake_provider_is_free() -> None:
    usage = {"input_characters": 10, "output_characters": 20}
    assert cost_of_usage("fake", "deterministic-fake-v1", usage, TABLE) == CostResult(
        0, "pricing-test"
    )


@pytest.mark.parametrize(
    ("provider", "model", "usage", "unknown"),
    [
        (
            "openai",
            "unpriced",
            {"prompt_tokens": 1, "completion_tokens": 1},
            "price:openai/unpriced",
        ),
        ("openai", "llm", {}, "usage.tokens"),
        ("openai", "tts", {"characters": 10}, "usage.audio_seconds"),
        ("elevenlabs", "voice", {"audio_seconds": 1.0}, "usage.characters"),
        ("stt.example", "whisper", {"characters": 3}, "usage.audio_seconds"),
    ],
)
def test_missing_price_or_usage_is_unknown_never_zero(
    provider: str, model: str, usage: dict[str, object], unknown: str
) -> None:
    result = cost_of_usage(provider, model, usage, TABLE)
    assert result.cost_micro_eur is None
    assert result.unknown_components == (unknown,)


def test_without_a_price_list_every_cost_is_unknown() -> None:
    assert cost_of_usage("fake", "deterministic-fake-v1", {}, None) == CostResult(
        None, None, ("pricing",)
    )


def test_shipped_price_list_loads_and_prices_the_default_models() -> None:
    settings = Settings(_env_file=None)
    table = load_pricing(settings.pricing_path)
    assert table.version == "pricing-v2"
    for model in {settings.patient_model, settings.evaluation_model, settings.content_model}:
        assert ("openai", model) in table.models
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    luna = cost_of_usage("openai", "gpt-6-luna", usage, table)
    assert luna.cost_micro_eur == round((0.10 + 0.50) * table.to_eur["USD"] * 1_000_000)
    groq = cost_of_usage("api.groq.com", "whisper-large-v3", {"audio_seconds": 2.0}, table)
    assert groq.cost_micro_eur == round(10 / 60 * 0.00185 * table.to_eur["USD"] * 1_000_000)


def test_price_list_rejects_units_that_do_not_fit_the_billing(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "version: v\nrecorded_on: '2026-10-15'\nto_eur: {USD: 0.9}\nmodels:\n"
        "  - {provider: p, model: m, billing: stt_minute, currency: USD,"
        " prices: {output_token: 1.0}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_pricing(path)


def test_price_list_rejects_a_currency_without_rate(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "version: v\nrecorded_on: '2026-10-15'\nto_eur: {EUR: 1.0}\nmodels:\n"
        "  - {provider: p, model: m, billing: tokens, currency: USD, prices: {input_token: 1.0}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_pricing(path)
