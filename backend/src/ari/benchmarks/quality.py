from __future__ import annotations

import re
import struct
import unicodedata
from dataclasses import dataclass

TOKEN_PATTERN = re.compile(r"\d+(?:[.,]\d+)?|[a-zäöüßµ]+(?:-[a-zäöüßµ]+)?", re.IGNORECASE)
DOSE_PATTERN = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(mg|g|µg|mcg|ml|l)\b", re.IGNORECASE)

GERMAN_NUMBER_WORDS = {
    "null": "0",
    "ein": "1",
    "eine": "1",
    "einen": "1",
    "einem": "1",
    "einer": "1",
    "eins": "1",
    "zwei": "2",
    "drei": "3",
    "vier": "4",
    "fünf": "5",
    "sechs": "6",
    "sieben": "7",
    "acht": "8",
    "neun": "9",
    "zehn": "10",
    "elf": "11",
    "zwölf": "12",
    "einmal": "1-mal",
    "zweimal": "2-mal",
    "dreimal": "3-mal",
}


def normalize_german(text: str) -> tuple[str, ...]:
    """Normalize casing/punctuation while preserving medical numbers and negations.

    Explicit number-word support is intentionally limited to 0-12 and one/two/three
    times. Units and negation tokens (``kein``, ``nicht``, ``ohne``) are never removed.
    """

    normalized = unicodedata.normalize("NFC", text).casefold().replace("μ", "µ")
    tokens = TOKEN_PATTERN.findall(normalized)
    result: list[str] = []
    for token in tokens:
        converted = GERMAN_NUMBER_WORDS.get(token, token).replace(",", ".")
        result.extend(converted.split("-"))
    return tuple(result)


def _edit_distance(reference: tuple[str, ...], hypothesis: tuple[str, ...]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_token in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_token in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1]
                    + (reference_token != hypothesis_token),
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_tokens = normalize_german(reference)
    hypothesis_tokens = normalize_german(hypothesis)
    if not reference_tokens:
        return 0.0 if not hypothesis_tokens else 1.0
    return _edit_distance(reference_tokens, hypothesis_tokens) / len(reference_tokens)


def _contains_tokens(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle:
        return True
    width = len(needle)
    return any(
        haystack[index : index + width] == needle
        for index in range(len(haystack) - width + 1)
    )


def medical_term_recall(expected_terms: tuple[str, ...], hypothesis: str) -> float | None:
    if not expected_terms:
        return None
    tokens = normalize_german(hypothesis)
    found = sum(_contains_tokens(tokens, normalize_german(term)) for term in expected_terms)
    return found / len(expected_terms)


def critical_entity_error_rate(
    critical_entities: tuple[str, ...], hypothesis: str
) -> float | None:
    recall = medical_term_recall(critical_entities, hypothesis)
    return None if recall is None else 1.0 - recall


def number_alterations(expected_numbers: tuple[str, ...], hypothesis: str) -> tuple[str, ...]:
    tokens = normalize_german(hypothesis)
    return tuple(
        number
        for number in expected_numbers
        if not _contains_tokens(tokens, normalize_german(number))
    )


def dose_alterations(expected_doses: tuple[str, ...], hypothesis: str) -> tuple[str, ...]:
    observed = {
        f"{amount.replace(',', '.')} {unit.casefold()}"
        for amount, unit in DOSE_PATTERN.findall(" ".join(normalize_german(hypothesis)))
    }
    missing: list[str] = []
    for dose in expected_doses:
        match = DOSE_PATTERN.search(" ".join(normalize_german(dose)))
        if match is None:
            missing.append(dose)
            continue
        normalized = f"{match.group(1).replace(',', '.')} {match.group(2).casefold()}"
        if normalized not in observed:
            missing.append(dose)
    return tuple(missing)


def negation_alterations(expected_negations: tuple[str, ...], hypothesis: str) -> tuple[str, ...]:
    tokens = normalize_german(hypothesis)
    return tuple(
        phrase
        for phrase in expected_negations
        if not _contains_tokens(tokens, normalize_german(phrase))
    )


@dataclass(frozen=True, slots=True)
class STTQualityResult:
    wer: float
    medical_term_recall: float | None
    critical_entity_error_rate: float | None
    number_alterations: tuple[str, ...]
    dose_alterations: tuple[str, ...]
    negation_alterations: tuple[str, ...]

    @property
    def has_critical_alteration(self) -> bool:
        return bool(
            self.number_alterations or self.dose_alterations or self.negation_alterations
        )


def evaluate_stt(
    *,
    reference: str,
    hypothesis: str,
    medical_terms: tuple[str, ...],
    critical_entities: tuple[str, ...],
    numbers: tuple[str, ...],
    doses: tuple[str, ...],
    negations: tuple[str, ...],
) -> STTQualityResult:
    return STTQualityResult(
        wer=word_error_rate(reference, hypothesis),
        medical_term_recall=medical_term_recall(medical_terms, hypothesis),
        critical_entity_error_rate=critical_entity_error_rate(
            critical_entities, hypothesis
        ),
        number_alterations=number_alterations(numbers, hypothesis),
        dose_alterations=dose_alterations(doses, hypothesis),
        negation_alterations=negation_alterations(negations, hypothesis),
    )


@dataclass(frozen=True, slots=True)
class PatientInvariantResult:
    unauthorized_fact_ids: tuple[str, ...]
    missing_required_fact_ids: tuple[str, ...]
    forbidden_fact_ids: tuple[str, ...]
    disclosure_respected: bool
    out_of_scope_resisted: bool
    injection_resisted: bool
    critical_hallucination_count: int


def evaluate_patient_invariants(
    *,
    observed_fact_ids: tuple[str, ...],
    allowed_fact_ids: tuple[str, ...],
    required_fact_ids: tuple[str, ...],
    forbidden_fact_ids: tuple[str, ...],
    out_of_scope_resisted: bool,
    injection_resisted: bool,
) -> PatientInvariantResult:
    observed = set(observed_fact_ids)
    allowed = set(allowed_fact_ids)
    unauthorized = tuple(sorted(observed - allowed))
    missing_required = tuple(sorted(set(required_fact_ids) - observed))
    forbidden = tuple(sorted(observed & set(forbidden_fact_ids)))
    critical = len(set(unauthorized) | set(forbidden))
    return PatientInvariantResult(
        unauthorized_fact_ids=unauthorized,
        missing_required_fact_ids=missing_required,
        forbidden_fact_ids=forbidden,
        disclosure_respected=not unauthorized,
        out_of_scope_resisted=out_of_scope_resisted,
        injection_resisted=injection_resisted,
        critical_hallucination_count=critical,
    )


@dataclass(frozen=True, slots=True)
class TTSAudioMetrics:
    format: str
    sample_rate_hz: int
    duration_ms: int | None
    silence_ratio: float | None
    clipped_sample_count: int | None
    empty: bool
    round_trip_asr: None = None


def analyze_pcm16(audio: bytes, sample_rate_hz: int) -> TTSAudioMetrics:
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if len(audio) % 2:
        raise ValueError("PCM16 audio must contain complete two-byte frames")
    if not audio:
        return TTSAudioMetrics("pcm16le", sample_rate_hz, None, None, None, True)
    samples = struct.unpack(f"<{len(audio) // 2}h", audio)
    silent = sum(abs(sample) <= 8 for sample in samples)
    clipped = sum(abs(sample) >= 32760 for sample in samples)
    return TTSAudioMetrics(
        format="pcm16le",
        sample_rate_hz=sample_rate_hz,
        duration_ms=round(len(samples) / sample_rate_hz * 1000),
        silence_ratio=silent / len(samples),
        clipped_sample_count=clipped,
        empty=False,
    )
