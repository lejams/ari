"""Load the versioned price list (`ari/pricing/pricing-vN.yaml`) into a `PricingTable`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ari.application.services.costing import (
    PRICE_UNITS,
    TOKEN_UNITS,
    Billing,
    ModelPrice,
    PricingTable,
)

# Units as written in the file, converted to the price of one unit.
_PER_UNIT = {
    **dict.fromkeys(TOKEN_UNITS, 1_000_000),
    "stt_minute": 1,
    "tts_minute": 1,
    "tts_character": 1_000,
}
_BILLING_UNITS: dict[str, frozenset[str]] = {
    "tokens": frozenset(TOKEN_UNITS),
    "stt_minute": frozenset({"stt_minute"}),
    "tts_minute": frozenset({"tts_minute"}),
    "tts_character": frozenset({"tts_character"}),
    "free": frozenset(),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ModelEntry(_Strict):
    provider: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    billing: Billing
    currency: Annotated[str, Field(min_length=3, max_length=3)]
    prices: dict[str, Annotated[float, Field(ge=0)] | None]
    minimum_billed_seconds: Annotated[float, Field(ge=0)] = 0.0

    @model_validator(mode="after")
    def units_match_billing(self) -> _ModelEntry:
        allowed = _BILLING_UNITS[self.billing]
        unexpected = set(self.prices) - allowed
        if unexpected:
            raise ValueError(f"{self.model}: units {sorted(unexpected)} do not fit {self.billing}")
        if self.billing != "free" and self.billing != "tokens" and not self.prices:
            raise ValueError(f"{self.model}: a {self.billing} model needs its unit price")
        return self


class _PricingFile(_Strict):
    version: Annotated[str, Field(min_length=1)]
    recorded_on: str
    to_eur: dict[str, Annotated[float, Field(gt=0)]]
    models: list[_ModelEntry]

    @model_validator(mode="after")
    def consistent(self) -> _PricingFile:
        keys = [(m.provider, m.model) for m in self.models]
        if len(keys) != len(set(keys)):
            raise ValueError("A provider/model pair is priced twice")
        for entry in self.models:
            if entry.currency not in self.to_eur:
                raise ValueError(f"{entry.model}: no EUR rate for {entry.currency}")
        return self


if set(_PER_UNIT) != set(PRICE_UNITS) or set(_BILLING_UNITS) != set(get_args(Billing)):
    raise RuntimeError("The price units of the loader and of costing.py diverged")


def load_pricing(path: Path) -> PricingTable:
    data = _PricingFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    models = {
        (entry.provider, entry.model): ModelPrice(
            provider=entry.provider,
            model=entry.model,
            billing=entry.billing,
            currency=entry.currency,
            unit_prices={
                unit: None if value is None else value / _PER_UNIT[unit]
                for unit, value in entry.prices.items()
            },
            minimum_billed_seconds=entry.minimum_billed_seconds,
        )
        for entry in data.models
    }
    return PricingTable(version=data.version, to_eur=dict(data.to_eur), models=models)
