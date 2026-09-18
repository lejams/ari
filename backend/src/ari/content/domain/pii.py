"""Deterministic detector of re-identifying details in a protocol record (`pii-detector-v1`).

An aid for the owner's validation, never a claim of anonymisation. Excerpts are masked so the
findings table itself never carries what it found.
"""

import re
from typing import Any, Literal

from ari.domain.clinical import ClinicalModel, Text
from ari.domain.clinical_privacy import CONTACT

PII_DETECTOR_VERSION = "pii-detector-v1"
PiiKind = Literal["contact", "exact_date", "person", "institution", "address", "postcode_city"]

PATTERNS: tuple[tuple[PiiKind, re.Pattern[str]], ...] = (
    ("contact", CONTACT),
    ("exact_date", re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.\s?(?:19|20)\d{2}\b")),
    (
        "person",
        re.compile(
            r"\b(?:Herr|Frau|Hr\.|Fr\.|Dr\.(?:\s?med\.)?|Prof\.|Oberarzt|Oberärztin|Chefarzt|"
            r"Chefärztin|Prüfer|Prüferin)\s+[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?\b"
        ),
    ),
    (
        "institution",
        re.compile(
            r"\b(?:Klinikum|Universitätsklinikum|Uniklinik|Universitätsklinik|Krankenhaus|"
            r"Charité|Ärztekammer|Landesärztekammer)\s+[A-ZÄÖÜ][\w-]+"
        ),
    ),
    (
        "address",
        re.compile(
            r"\b[A-ZÄÖÜ][\wäöüß-]*[\s-]?"
            r"(?:[Ss]traße|[Ss]trasse|[Ss]tr\.|[Ww]eg|[Pp]latz|[Aa]llee|[Gg]asse|[Rr]ing|[Dd]amm)"
            r"\s+\d+"
        ),
    ),
    ("postcode_city", re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß-]+\b")),
)


class PiiFinding(ClinicalModel):
    kind: PiiKind
    path: Text
    excerpt: Text  # Masked: enough to locate, not enough to re-identify.


def _mask(match: str) -> str:
    return match[:2] + "…" * min(3, max(1, len(match) - 2))


def scan(value: Any, path: str = "") -> tuple[PiiFinding, ...]:
    if isinstance(value, str):
        return tuple(
            PiiFinding(kind=kind, path=path or "$", excerpt=_mask(hit.group(0)))
            for kind, pattern in PATTERNS
            for hit in pattern.finditer(value)
        )
    if isinstance(value, dict):
        return tuple(
            finding
            for key, item in value.items()
            for finding in scan(item, f"{path}.{key}" if path else str(key))
        )
    if isinstance(value, list | tuple):
        return tuple(
            finding
            for index, item in enumerate(value)
            for finding in scan(item, f"{path}[{index}]")
        )
    return ()
