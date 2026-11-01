"""evaluation-language@2: what the scorer counts for duplicates, location, categories, stability."""

from __future__ import annotations

import json
from typing import Any

import pytest
from scripted import DATASETS, GATES, ScriptedProvider, item_id_of, repetition_of, request_for

from ari.application.contracts import LLMRequest
from ari.evals.gates import load_gates
from ari.evals.manifest import load_dataset
from ari.evals.runner import run
from ari.evals.scorers.evaluation_language import EvaluationLanguageSuite, LanguageItem

V2 = load_dataset(DATASETS, "evaluation-language", "2")
GATE_RULES, _ = load_gates(GATES)


def _items(split: str = "dev") -> dict[str, LanguageItem]:
    return {
        row["id"]: LanguageItem.model_validate_json(json.dumps(row))  # type: ignore[misc]
        for row in V2.read_split(split, gate=split == "holdout")  # type: ignore[arg-type]
    }


ITEMS = _items()


def report_of(
    item: LanguageItem, turn: int, category: str, correction: str | None = None
) -> dict[str, Any]:
    spoken = next(t.doctor for t in item.turns if t.sequence == turn)
    truth = next((e for e in item.truth.language_errors if e.turn == turn), None)
    return {
        "text": "Fehler",
        "category": category,
        "quote": spoken[:120],
        "correction": (correction or (truth.correct if truth else "x"))[:120],
        "evidence_turn_sequences": [turn],
    }


def output(
    errors: list[dict[str, Any]], truth_switches: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "summary": "Synthetische Rückmeldung.",
        "language_errors": errors,
        "code_switches": truth_switches or [],
        "empathy": [],
    }


def switches(item: LanguageItem) -> list[dict[str, Any]]:
    return [
        {"turn": c.turn, "fragment": "frz.", "intended_term": c.intended_term}
        for c in item.truth.code_switches
    ]


async def measure(respond: Any, repetitions: int = 1) -> Any:
    provider = ScriptedProvider(respond)
    return await run(
        request_for(EvaluationLanguageSuite(), provider, dataset=V2, repetitions=repetitions)
    )


async def test_the_oracle_is_perfect_and_every_gated_category_has_support() -> None:
    def oracle(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS[item_id_of(request)]
        return output(
            [report_of(item, e.turn, e.category) for e in item.truth.language_errors],
            switches(item),
        )

    report = await measure(oracle, repetitions=2)
    for category in ("gender", "case", "verb_form", "word_order", "word_choice", "register"):
        assert report.metrics[f"language.precision.{category}"] == 1.0
        assert report.metrics[f"language.recall.{category}"] == 1.0
    assert report.metrics["language_location.precision"] == 1.0
    assert report.metrics["language_location.recall"] == 1.0
    assert report.metrics["category_accuracy_given_located"] == 1.0
    assert report.metrics["stability.changed_rate"] == 0.0
    assert report.versions.scorer == "evaluation-language-scorer-v2"


async def test_a_wrong_label_on_the_right_passage_costs_the_category_not_the_location() -> None:
    """Ismail's reading of the first run: found at the right place, labelled differently."""

    def mislabelled(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS[item_id_of(request)]
        return output(
            [
                report_of(item, e.turn, "word_choice" if e.category == "case" else e.category)
                for e in item.truth.language_errors
            ],
            switches(item),
        )

    report = await measure(mislabelled)
    assert report.metrics["language.recall.case"] == 0.0
    assert report.metrics["language_location.precision"] == 1.0
    assert report.metrics["language_location.recall"] == 1.0
    accuracy = report.metrics["category_accuracy_given_located"]
    assert accuracy is not None and 0.0 < accuracy < 1.0


async def test_a_passage_reported_twice_is_one_report_and_one_stable_verdict() -> None:
    def doubled(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS[item_id_of(request)]
        errors = [report_of(item, e.turn, e.category) for e in item.truth.language_errors]
        if repetition_of(request) == 1:
            # Same passage, same category, another quote: the evaluator keeps both (different
            # words), the scorer counts one report and a duplicate that is neither right nor wrong.
            errors += [{**e, "quote": e["quote"][:20]} for e in errors]
        return output(errors[:6], switches(item))

    report = await measure(doubled, repetitions=3)
    assert report.metrics["language_overall.precision"] == 1.0
    assert report.metrics["evidence_hallucination_rate"] == 0.0
    assert report.metrics["category_confusion_rate"] == 0.0
    assert report.metrics["stability.changed_rate"] == 0.0


async def test_a_second_passage_in_an_error_turn_is_a_false_error() -> None:
    def extra(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS[item_id_of(request)]
        errors = [report_of(item, e.turn, e.category) for e in item.truth.language_errors]
        if item.truth.language_errors:
            turn = item.truth.language_errors[0].turn
            spoken = next(t.doctor for t in item.turns if t.sequence == turn)
            # The reported stretch is the first words; another stretch of the same turn, same
            # category and a made-up correction, is a second passage.
            words = spoken.split()
            errors[0] = {**errors[0], "quote": " ".join(words[:2])}
            errors.append({**errors[0], "quote": words[-1], "correction": "x"})
        return output(errors[:6], switches(item))

    report = await measure(extra)
    precision = report.metrics["language_overall.precision"]
    assert precision is not None and precision < 1.0


async def test_an_extra_passage_on_one_repetition_makes_the_item_unstable() -> None:
    def flaky(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS[item_id_of(request)]
        errors = [report_of(item, e.turn, e.category) for e in item.truth.language_errors]
        if item.truth.language_errors:
            spoken = next(
                t.doctor
                for t in item.turns
                if t.sequence == errors[0]["evidence_turn_sequences"][0]
            )
            words = spoken.split()
            errors[0] = {**errors[0], "quote": " ".join(words[:2])}
            if repetition_of(request) == 1:
                errors.append({**errors[0], "quote": words[-1], "correction": "x"})
        return output(errors[:6], switches(item))

    report = await measure(flaky, repetitions=3)

    changed = sum(1 for i in ITEMS.values() if i.truth.language_errors)
    assert report.metrics["stability.changed_rate"] == pytest.approx(changed / len(ITEMS))


async def test_an_error_reported_on_a_code_switch_turn_is_neutral_and_not_part_of_stability() -> (
    None
):
    def remark(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS[item_id_of(request)]
        errors = [report_of(item, e.turn, e.category) for e in item.truth.language_errors]
        if repetition_of(request) == 1:
            errors += [
                report_of(item, c.turn, "word_choice", "x") for c in item.truth.code_switches
            ]
        return output(errors[:6], switches(item))

    report = await measure(remark, repetitions=3)
    assert report.metrics["language_overall.precision"] == 1.0
    capped = sum(
        1
        for i in ITEMS.values()
        if i.truth.code_switches and len(i.truth.language_errors) + len(i.truth.code_switches) > 6
    )
    assert report.metrics["stability.changed_rate"] == pytest.approx(capped / len(ITEMS))


async def test_correct_but_unusual_sentences_are_hard_negatives() -> None:
    natural = {
        item.id: [
            t.sequence
            for t in item.turns
            if t.sequence not in {e.turn for e in item.truth.language_errors}
            and t.sequence not in {c.turn for c in item.truth.code_switches}
            and t.sequence not in set(item.truth.stt_artifact_turns)
        ]
        for item in ITEMS.values()
    }

    def over_correct(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS[item_id_of(request)]
        errors = [report_of(item, e.turn, e.category) for e in item.truth.language_errors]
        errors.append(report_of(item, natural[item.id][0], "word_order", "x"))
        return output(errors[:6], switches(item))

    report = await measure(over_correct)
    precision = report.metrics["language.precision.word_order"]
    assert precision is not None and precision < 1.0
    hallucination = report.metrics["evidence_hallucination_rate"]
    assert hallucination is not None and hallucination > 0.0


async def test_a_category_nobody_injects_weighs_on_the_overall_precision_only() -> None:
    def other(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = ITEMS[item_id_of(request)]
        errors = [report_of(item, e.turn, e.category) for e in item.truth.language_errors]
        errors.append(report_of(item, item.turns[-1].sequence, "other", "x"))
        return output(errors[:6], switches(item))

    report = await measure(other)
    assert "language.precision.other" not in report.metrics
    overall = report.metrics["language_overall.precision"]
    assert overall is not None and overall < 1.0


async def test_the_gate_of_version_2_fails_on_an_over_correcting_evaluator() -> None:
    holdout = _items("holdout")

    def over_correct(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = holdout[item_id_of(request)]
        errors = [report_of(item, e.turn, e.category) for e in item.truth.language_errors]
        errors.append(report_of(item, item.turns[0].sequence, "other", "x"))
        return output(errors[:6], switches(item))

    request = request_for(
        EvaluationLanguageSuite(),
        ScriptedProvider(over_correct),
        dataset=V2,
        split="holdout",
        gate_mode=True,
        gates=GATE_RULES,
        repetitions=3,
    )
    report = await run(request)
    assert report.gate is not None and report.gate.status == "failed"
    failed = {c.metric for c in report.gate.checks if c.status == "failed"}
    assert "language_overall.precision" in failed or "evidence_hallucination_rate" in failed


def test_the_evaluation_prompt_quotes_no_sentence_of_the_dataset() -> None:
    """The examples of the prompt are never the test: a measure on the dev split would then
    reward memorising, and the holdout would not be independent."""
    import re

    from ari.config import PROJECT_ROOT

    prompt = (PROJECT_ROOT / "backend/src/ari/prompts/evaluation_v10.txt").read_text("utf-8")
    quoted = [q for q in re.findall(r'"([^"]{12,})"', prompt) if any(c.isalpha() for c in q)]
    assert quoted  # The prompt does carry German examples.
    spoken: list[str] = []
    for split in ("dev", "holdout"):
        spoken += [
            t.doctor.casefold().rstrip("?.!") for item in _items(split).values() for t in item.turns
        ] + [
            error.correct.casefold().rstrip("?.!")
            for item in _items(split).values()
            for error in item.truth.language_errors
        ]
    for example in quoted:
        needle = example.casefold().rstrip("?.!")
        assert not any(needle in line for line in spoken), example
