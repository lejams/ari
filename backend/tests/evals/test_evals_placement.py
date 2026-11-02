"""placement@1: what the scorer counts for the spoken placement rater."""

from __future__ import annotations

import json
import re
from typing import Any

from scripted import DATASETS, GATES, ScriptedProvider, request_for

from ari.config import PROJECT_ROOT
from ari.evals.gates import load_gates
from ari.evals.manifest import load_dataset
from ari.evals.runner import run
from ari.evals.scorers.placement import PlacementSuite, SpokenItem

DATASET = load_dataset(DATASETS, "placement", "1")
GATE_RULES, _ = load_gates(GATES)


def _items(split: str) -> list[SpokenItem]:
    return [
        SpokenItem.model_validate_json(json.dumps(row))
        for row in DATASET.read_split(split, gate=split == "holdout")  # type: ignore[arg-type]
    ]


BY_TRANSCRIPT = {i.transcript: i for split in ("dev", "holdout") for i in _items(split)}


def oracle(
    *, level_shift: int = 0, drop: int = 0, category: str | None = None, invent: bool = False
) -> Any:
    levels = ["A1", "A2", "B1", "B2", "C1", "C2"]

    def respond(_request: Any, payload: dict[str, Any]) -> dict[str, Any]:
        item = BY_TRANSCRIPT[payload["transcript"]]
        errors = [
            {
                "text": "Fehler",
                "evidence_turn_sequences": [1],
                "quote": e.quote,
                "correction": e.correction,
                "category": category or e.category,
            }
            for e in item.truth.errors
        ][drop:]
        if invent:
            errors.append(
                {
                    "text": "Erfunden",
                    "evidence_turn_sequences": [1],
                    "quote": "dieser Satz wurde nie gesagt",
                    "correction": "dieser Satz wurde nie gesagt, ja",
                    "category": "case",
                }
            )
        return {
            "estimated_level": levels[levels.index(item.expected_level) + level_shift],
            "confidence": 0.8,
            "observations": [],
            "language_errors": errors,
        }

    return respond


async def measure(respond: Any, *, split: str = "dev", gate: bool = False) -> Any:
    return await run(
        request_for(
            PlacementSuite(),
            ScriptedProvider(respond),
            dataset=DATASET,
            split=split,
            gate_mode=gate,
            gates=GATE_RULES,
            repetitions=3 if gate else 1,
        )
    )


async def test_the_oracle_is_perfect_and_passes_the_gate_on_the_holdout() -> None:
    report = await measure(oracle(), split="holdout", gate=True)
    assert report.gate is not None and report.gate.status == "passed", report.gate
    metrics = report.metrics
    for name in (
        "language_overall.precision",
        "language_overall.recall",
        "weak_point.precision.all",
        "weak_point.recall.all",
        "level.within_one",
        "level.exact",
    ):
        assert metrics[name] == 1.0, name
    assert metrics["invented_quote_rate"] == 0.0


async def test_missing_the_errors_lowers_recall_and_the_weak_points() -> None:
    report = await measure(oracle(drop=1))
    assert report.metrics["language_overall.precision"] == 1.0
    assert report.metrics["language_overall.recall"] < 0.8
    assert report.metrics["weak_point.recall.all"] < 1.0


async def test_a_right_passage_with_a_wrong_category_is_a_confusion_not_a_hit() -> None:
    report = await measure(oracle(category="word_choice"))
    assert report.metrics["category_confusion_rate"] is not None
    assert report.metrics["language_overall.precision"] < 0.5
    outcomes = {o for item in report.items for o in item.outcomes}
    assert {"false_language_error", "missed_language_error"} <= outcomes


async def test_an_invented_quote_is_dropped_by_the_code_and_counted() -> None:
    report = await measure(oracle(invent=True))
    # The code never lets the invented error through: precision stays perfect...
    assert report.metrics["language_overall.precision"] == 1.0
    # ...and the model is held to account for proposing it.
    assert report.metrics["invented_quote_rate"] is not None
    assert report.metrics["invented_quote_rate"] > 0.05


async def test_the_level_is_judged_to_one_level() -> None:
    near = await measure(oracle(level_shift=-1))
    assert near.metrics["level.within_one"] == 1.0 and near.metrics["level.exact"] == 0.0
    far = await measure(oracle(level_shift=-2))
    assert far.metrics["level.within_one"] == 0.0
    assert any("level_off" in item.outcomes for item in far.items)


async def test_the_gate_fails_on_a_rater_that_finds_nothing() -> None:
    report = await measure(oracle(drop=6), split="holdout", gate=True)
    assert report.gate is not None and report.gate.status == "failed"
    failed = {c.metric for c in report.gate.checks if c.status == "failed"}
    assert {"language_overall.recall", "weak_point.recall.all"} <= failed


def test_truth_is_known_by_construction() -> None:
    for item in BY_TRANSCRIPT.values():
        for error in item.truth.errors:
            assert item.transcript.count(error.quote) == 1, item.id
            assert error.quote != error.correction
        categories = [e.category for e in item.truth.errors]
        assert len(categories) in (0, 2, 3, 6)
    # Every level is in both splits; every category is somewhere (the holdout topic has no
    # `case` or `register` error: the gate is on the overall rates, not on one category).
    for split in ("dev", "holdout"):
        assert {i.expected_level for i in _items(split)} == {"B1", "B2", "C1"}
    assert {e.category for i in BY_TRANSCRIPT.values() for e in i.truth.errors} == {
        "gender",
        "case",
        "verb_form",
        "word_order",
        "word_choice",
        "register",
    }


def test_the_speaking_prompt_quotes_no_sentence_of_the_dataset() -> None:
    prompt = (PROJECT_ROOT / "backend/src/ari/prompts/placement_speaking_v2.txt").read_text("utf-8")
    quoted = [q for q in re.findall(r'"([^"]{6,})"', prompt) if any(c.isalpha() for c in q)]
    spoken = [
        text.casefold()
        for item in BY_TRANSCRIPT.values()
        for text in (item.transcript, *(e.quote for e in item.truth.errors))
    ]
    for example in quoted:
        assert not any(example.casefold() in line for line in spoken), example
    # The category examples of the prompt are not the dataset's errors either.
    for example in ("dem Familie", "der Familie", "mich", "einnehmen Sie"):
        assert not any(
            example.casefold() in e.quote.casefold()
            for i in BY_TRANSCRIPT.values()
            for e in i.truth.errors
            if len(example) > 6
        ), example
