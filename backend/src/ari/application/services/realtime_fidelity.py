"""Deterministic verification that a Realtime transport spoke the canonical text."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def normalize_observed_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold().strip()
    value = re.sub(r"\s+", " ", value)
    return value.rstrip(".!? ")


@dataclass(frozen=True, slots=True)
class FidelityResult:
    matches: bool
    expected: str
    observed: str
    reason: str = "exact_normalized_match"


def verify_response_fidelity(expected: str, observed: str) -> FidelityResult:
    expected_n = normalize_observed_text(expected)
    observed_n = normalize_observed_text(observed)
    if expected_n == observed_n:
        return FidelityResult(True, expected_n, observed_n)
    return FidelityResult(False, expected_n, observed_n, "canonical_response_mismatch")

