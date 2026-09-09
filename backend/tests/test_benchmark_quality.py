from __future__ import annotations

import struct

import pytest

from ari.benchmarks.quality import (
    analyze_pcm16,
    dose_alterations,
    evaluate_patient_invariants,
    evaluate_stt,
    medical_term_recall,
    negation_alterations,
    number_alterations,
    word_error_rate,
)


def test_german_wer_and_explicit_number_word_equivalence() -> None:
    assert word_error_rate("Ramipril fünf mg", "Ramipril 5 mg") == 0
    assert word_error_rate("kein Fieber", "Fieber") == pytest.approx(0.5)
    assert dose_alterations(("5 mg",), "fünf mg") == ()
    assert word_error_rate("5 µg", "5 g") > 0
    assert dose_alterations(("5 µg",), "5 g") == ("5 µg",)


def test_medical_term_recall_distinguishes_close_medication_names() -> None:
    recall = medical_term_recall(("Ramipril", "Metformin"), "Ramipril und Metoprolol")

    assert recall == 0.5


def test_dose_number_frequency_and_negation_alterations_are_critical() -> None:
    assert dose_alterations(("5 mg",), "Ich nehme 50 mg.") == ("5 mg",)
    assert number_alterations(("zweimal",), "einmal täglich") == ("zweimal",)
    assert negation_alterations(("kein Fieber",), "Ich habe Fieber") == ("kein Fieber",)


def test_stt_quality_preserves_dose_number_and_negation_failures() -> None:
    result = evaluate_stt(
        reference="Ramipril 5 mg zweimal täglich, kein Fieber.",
        hypothesis="Ramipril 50 mg einmal täglich, Fieber.",
        medical_terms=("Ramipril",),
        critical_entities=("Ramipril",),
        numbers=("5", "zweimal"),
        doses=("5 mg",),
        negations=("kein Fieber",),
    )

    assert result.medical_term_recall == 1
    assert result.dose_alterations == ("5 mg",)
    assert result.has_critical_alteration is True


def test_patient_invariants_detect_forbidden_or_unauthorized_facts() -> None:
    result = evaluate_patient_invariants(
        observed_fact_ids=("allowed", "forbidden", "invented"),
        allowed_fact_ids=("allowed", "forbidden"),
        required_fact_ids=("allowed",),
        forbidden_fact_ids=("forbidden",),
        out_of_scope_resisted=True,
        injection_resisted=True,
    )

    assert result.unauthorized_fact_ids == ("invented",)
    assert result.forbidden_fact_ids == ("forbidden",)
    assert result.critical_hallucination_count == 2


def test_pcm16_quality_metrics_cover_duration_silence_clipping_and_empty() -> None:
    audio = struct.pack("<hhhh", 0, 4, 32767, -100)
    result = analyze_pcm16(audio, sample_rate_hz=4)

    assert result.duration_ms == 1000
    assert result.silence_ratio == 0.5
    assert result.clipped_sample_count == 1
    assert result.empty is False
    assert analyze_pcm16(b"", 24_000).empty is True
