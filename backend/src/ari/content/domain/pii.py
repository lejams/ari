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


_FOLLOWED_BY_NAME = re.compile(r"\s+[A-ZÄÖÜ]")


def _is_generic_address(text: str, hit: re.Match[str], generic: frozenset[str]) -> bool:
    """A form of address such as "Herr Doktor": a title, not a name, unless a name follows."""
    last_word = hit.group(0).split()[-1].lower()
    return last_word in generic and _FOLLOWED_BY_NAME.match(text, hit.end()) is None


def scan(
    value: Any, path: str = "", *, generic_titles: frozenset[str] = frozenset()
) -> tuple[PiiFinding, ...]:
    """Findings in a nested value.

    `generic_titles` (lower case) lists words that make a "person" hit a plain form of address
    when no name follows ("Herr Doktor"); by default nothing is excused, as for protocols.
    """
    if isinstance(value, str):
        return tuple(
            PiiFinding(kind=kind, path=path or "$", excerpt=_mask(hit.group(0)))
            for kind, pattern in PATTERNS
            for hit in pattern.finditer(value)
            if not (kind == "person" and _is_generic_address(value, hit, generic_titles))
        )
    if isinstance(value, dict):
        return tuple(
            finding
            for key, item in value.items()
            for finding in scan(
                item, f"{path}.{key}" if path else str(key), generic_titles=generic_titles
            )
        )
    if isinstance(value, list | tuple):
        return tuple(
            finding
            for index, item in enumerate(value)
            for finding in scan(item, f"{path}[{index}]", generic_titles=generic_titles)
        )
    return ()
