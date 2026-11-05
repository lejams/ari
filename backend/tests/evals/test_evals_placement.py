"""placement: what the scorer counts for the spoken placement rater (current version, 2)."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from scripted import DATASETS, GATES, ScriptedProvider, repetition_of, request_for

from ari.config import PROJECT_ROOT
from ari.evals.gates import load_gates
from ari.evals.manifest import load_dataset
from ari.evals.report import diagnosis_text
from ari.evals.runner import run
from ari.evals.scorers.placement import PlacementSuite, SpokenItem

DATASET = load_dataset(DATASETS, "placement", "2")
GATE_RULES, _ = load_gates(GATES)


def _items(split: str) -> list[SpokenItem]:
    return [
        SpokenItem.model_validate_json(json.dumps(row))
        for row in DATASET.read_split(split, gate=split == "holdout")  # type: ignore[arg-type]
    ]


BY_TRANSCRIPT = {i.transcript: i for split in ("dev", "holdout") for i in _items(split)}


def oracle(
    *,
    level_shift: int = 0,
    drop: int = 0,
    category: str | None = None,
    invent: bool = False,
    repeat: bool = False,
    widen_every_other_call: bool = False,
) -> Any:
    calls = {"n": 0}
    levels = ["A1", "A2", "B1", "B2", "C1", "C2"]

    def respond(_request: Any, payload: dict[str, Any]) -> dict[str, Any]:
        item = BY_TRANSCRIPT[payload["transcript"]]
        calls["n"] += 1
        wide = widen_every_other_call and calls["n"] % 2 == 0

        def quoted(e: Any) -> str:
            if not wide:
                return str(e.quote)
            sentences = re.split(r"(?<=[.!?])\s+", item.transcript)
            return next((s for s in sentences if e.quote in s), str(e.quote))

        errors = [
            {
                "text": "Fehler",
                "evidence_turn_sequences": [1],
                "quote": quoted(e),
                "correction": e.correction,
                "category": category or e.category,
            }
            for e in item.truth.errors
        ][drop:]
        if repeat:
            errors = [*errors, *errors[:1]]  # One real quote reported twice.
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


def forgetful(base: Any) -> Any:
    """The oracle, except it forgets the first error in the first call of the second repetition."""

    def respond(request: Any, payload: dict[str, Any]) -> dict[str, Any]:
        answer: dict[str, Any] = base(request, payload)
        if repetition_of(request) == 2 and request.sample == 0:
            answer["language_errors"] = answer["language_errors"][1:]
        return answer

    return respond


async def stability_with(passes: int, *, respond: Any = None) -> Any:
    request = request_for(
        PlacementSuite(),
        ScriptedProvider(respond or forgetful(oracle())),
        dataset=DATASET,
        split="holdout",
        gate_mode=True,
        gates=GATE_RULES,
        repetitions=3,
    )
    config = request.config.model_copy(update={"evaluation_passes": passes})
    return await run(replace(request, config=config))


async def test_the_diagnosis_reads_a_placement_dev_run() -> None:
    request = request_for(
        PlacementSuite(),
        ScriptedProvider(forgetful(oracle())),
        dataset=DATASET,
        split="dev",
        gate_mode=False,
        gates=GATE_RULES,
        repetitions=3,
    )
    text = diagnosis_text(await run(request))
    assert "instable" in text and "niveau lu" in text and "manqué [" in text
    assert "KeyError" not in text


async def test_a_single_forgetful_call_makes_the_rating_unstable() -> None:
    report = await stability_with(1)
    assert report.metrics["stability.changed_rate"] > 0.5
    assert report.gate is not None and report.gate.status == "failed"


async def test_three_passes_outvote_the_forgetful_call() -> None:
    report = await stability_with(3)
    assert report.metrics["stability.changed_rate"] == 0.0
    assert report.metrics["language_overall.recall"] == 1.0
    assert report.gate is not None and report.gate.status == "passed", report.gate


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


async def test_a_repeated_real_quote_is_not_invented_evidence() -> None:
    # The code drops the second report as a duplicate, but its quote stands in the transcript.
    report = await measure(oracle(repeat=True))
    assert report.metrics["language_overall.precision"] == 1.0
    assert report.metrics["invented_quote_rate"] == 0.0


async def test_other_quote_boundaries_for_the_same_error_are_not_instability() -> None:
    report = await measure(oracle(widen_every_other_call=True), split="holdout", gate=True)
    assert report.metrics["language_overall.recall"] == 1.0
    assert report.metrics["stability.changed_rate"] == 0.0


def flaky(base: Any, *, level: bool = False, first_error: bool = False) -> Any:
    """The oracle, except that in the second repetition it reads one level lower, or forgets the
    first error, on every call (so the passes agree and the rating itself changes)."""

    def respond(request: Any, payload: dict[str, Any]) -> dict[str, Any]:
        answer: dict[str, Any] = base(request, payload)
        if repetition_of(request) == 2:
            if level:
                levels = ["A1", "A2", "B1", "B2", "C1", "C2"]
                answer["estimated_level"] = levels[levels.index(answer["estimated_level"]) - 1]
            if first_error:
                answer["language_errors"] = answer["language_errors"][1:]
        return answer

    return respond


async def test_stability_is_measured_on_each_part_the_product_uses() -> None:
    steady = await stability_with(1, respond=oracle())
    for name in (
        "stability.level.changed_rate",
        "stability.weak_points.changed_rate",
        "stability.errors.changed_rate",
        "stability.errors.flip_rate",
        "stability.level.flip_rate",
    ):
        assert steady.metrics[name] == 0.0, name

    level = await stability_with(1, respond=flaky(oracle(), level=True))
    assert level.metrics["stability.level.changed_rate"] == 1.0
    # One level, two labels seen (the two levels read), none in every repetition.
    assert level.metrics["stability.level.flip_rate"] == 1.0
    assert level.metrics["stability.errors.flip_rate"] == 0.0
    assert level.metrics["stability.weak_points.changed_rate"] == 0.0

    error = await stability_with(1, respond=flaky(oracle(), first_error=True))
    assert error.metrics["stability.level.changed_rate"] == 0.0
    # The whole signature changes on every item with an error...
    assert error.metrics["stability.changed_rate"] == 48 / 60
    # ...while each error is counted once: one error out of 2, 2, 3 or 6 flips per item.
    flips = error.metrics["stability.errors.flip_rate"]
    assert flips is not None and abs(flips - 16 / 52) < 1e-9
    failed = {c.metric for c in error.gate.checks if c.status == "failed"} if error.gate else set()
    assert "stability.errors.flip_rate" in failed and "stability.changed_rate" not in failed


async def test_one_error_reported_twice_on_two_parts_of_its_quote_is_a_duplicate() -> None:
    def twice(request: Any, payload: dict[str, Any]) -> dict[str, Any]:
        answer: dict[str, Any] = oracle()(request, payload)
        errors = answer["language_errors"]
        words = errors[0]["quote"].split() if errors else []
        if len(words) >= 2:  # The first and the last word of the quote, as two reports.
            errors[0] = {**errors[0], "quote": words[0]}
            errors.append({**errors[0], "quote": words[-1]})
        return answer

    report = await stability_with(1, respond=twice)
    assert report.metrics["language_overall.precision"] == 1.0
    assert report.metrics["language_overall.recall"] == 1.0
    assert report.metrics["duplicate_rate"] is not None and report.metrics["duplicate_rate"] > 0
    assert report.metrics["stability.errors.flip_rate"] == 0.0


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
    # Every level and every category is in both splits (version 1's holdout had no `case` or
    # `register` error), and the holdout is large enough for a 10 % gate (60 transcripts).
    for split in ("dev", "holdout"):
        assert {i.expected_level for i in _items(split)} == {"B1", "B2", "C1"}
        assert {e.category for i in _items(split) for e in i.truth.errors} == {
            "gender",
            "case",
            "verb_form",
            "word_order",
            "word_choice",
            "register",
        }
    assert len(_items("holdout")) == 60
    assert not {i.transcript for i in _items("dev")} & {i.transcript for i in _items("holdout")}


def test_version_2_replaces_the_two_doubtful_labels_of_version_1() -> None:
    quotes = {e.quote for i in BY_TRANSCRIPT.values() for e in i.truth.errors}
    assert "Wenn ich mehr Zeit habe" not in quotes and "den gesamten Team" not in quotes
    assert {"an den Schichtdienst gewöhnen", "das gesamte Team"} <= quotes


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
