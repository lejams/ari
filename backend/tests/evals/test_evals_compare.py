"""Gates, comparison and the cost / quality frontier (plan, action 8, point 5bis)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from scripted import GATES, ScriptedProvider, config, item_id_of, request_for
from test_evals_suites import oracle_patient

from ari.application.contracts import LLMRequest
from ari.config import PROJECT_ROOT
from ari.evals.compare import CompareError, compare, render_markdown
from ari.evals.gates import Gates, evaluate, load_gates
from ari.evals.metrics import Ratio, bootstrap_interval, paired_difference_interval
from ari.evals.report import Report, read_report
from ari.evals.runner import run
from ari.evals.scorers.patient_leakage import PatientLeakageSuite

GATE_RULES, _ = load_gates(GATES)
SUITE = PatientLeakageSuite()


def _gates(thresholds: list[dict[str, Any]], critical: list[str] | None = None) -> Gates:
    return Gates.model_validate(
        {
            "version": "test",
            "repetitions": 3,
            "suites": {"s@1": {"thresholds": thresholds, "critical_outcomes": critical or []}},
        }
    )


def test_unmeasured_metric_fails_and_wildcards_cover_every_category() -> None:
    gates = _gates(
        [
            {"metric": "language.precision.*", "min": 0.85, "source": "t"},
            {"metric": "leak_rate", "max": 0.02, "source": "t"},
        ]
    )
    result = evaluate(
        gates,
        "s@1",
        {
            "language.precision.case": 0.9,
            "language.precision.gender": 0.5,
            "language.precision.other": None,
            "leak_rate": None,
        },
        [],
    )
    statuses = {c.metric: c.status for c in result.checks}
    assert statuses == {
        "language.precision.case": "passed",
        "language.precision.gender": "failed",
        "leak_rate": "not_measured",
    }
    assert result.status == "failed"
    nothing = evaluate(gates, "s@1", {"leak_rate": 0.0}, [])
    assert {c.metric: c.status for c in nothing.checks}["language.precision.*"] == "not_measured"


def test_only_a_new_critical_error_blocks() -> None:
    gates = _gates([{"metric": "m", "min": 0.5, "source": "t"}], ["invented_fact"])
    existing = ["a:invented_fact"]
    assert evaluate(gates, "s@1", {"m": 1.0}, existing).status == "failed"
    kept = evaluate(gates, "s@1", {"m": 1.0}, existing, baseline_critical=existing)
    assert kept.status == "passed"
    new = evaluate(
        gates, "s@1", {"m": 1.0}, ["a:invented_fact", "b:invented_fact"], baseline_critical=existing
    )
    assert new.status == "failed" and new.new_critical_errors == ["b:invented_fact"]


def test_bootstrap_intervals_are_seeded_and_bracket_the_value() -> None:
    ratio = Ratio("r", ("hit",), ("n",))
    items = [{"hit": float(i % 3 == 0), "n": 1.0} for i in range(30)]
    interval = bootstrap_interval(ratio, items)
    assert interval is not None and interval == bootstrap_interval(ratio, items)
    assert interval[0] <= ratio.value({"hit": 10.0, "n": 30.0}) <= interval[1]  # type: ignore[operator]
    same = paired_difference_interval(ratio, items, items)
    assert same == (0.0, 0.0)
    assert bootstrap_interval(ratio, [{"n": 0.0}]) is None


async def _report(config_id: str, provider: ScriptedProvider, **extra: Any) -> Report:
    request = request_for(SUITE, provider, repetitions=3, gates=GATE_RULES, **extra)
    fake = provider._provider == "fake"
    the_config = config(config_id, None if fake else provider._model, provider._provider)
    return await run(replace(request, config=the_config))


def _leaky(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
    if item_id_of(request).endswith(("-12", "-13")):
        return {"response_kind": "sources", "source_refs": ["fact:identity"]}
    return oracle_patient(request, payload)


async def test_the_cheapest_configuration_that_passes_every_gate_is_recommended() -> None:
    terra = await _report("terra", ScriptedProvider(oracle_patient, model="gpt-5.6-terra"))
    luna = await _report("luna", ScriptedProvider(oracle_patient, model="gpt-6-luna"))
    # Cheapest of all (free fake pricing) but leaks: never recommended.
    cheap = await _report(
        "leaky-free", ScriptedProvider(_leaky, provider="fake", model="deterministic-fake-v1")
    )
    # Passes, but gpt-5.6-luna has no price: listed apart, never ranked first.
    unpriced = await _report("unpriced", ScriptedProvider(oracle_patient, model="gpt-5.6-luna"))

    comparison = compare([terra, luna, cheap, unpriced], GATE_RULES)

    assert comparison.baseline == "terra"
    assert comparison.recommendation == "luna"
    assert [(r.rank, r.config_id) for r in comparison.ranking] == [
        (1, "luna"),
        (2, "terra"),
        (None, "unpriced"),
    ]
    assert terra.cost.mean_micro_eur_per_case == 3872
    assert luna.cost.mean_micro_eur_per_case == 176
    assert unpriced.cost.mean_micro_eur_per_case is None
    assert any("leak_rate" in reason for reason in comparison.rejected["leaky-free"])
    leaky = next(c for c in comparison.candidates if c.config_id == "leaky-free")
    assert len(leaky.verdict_changes) == 6  # Items -12 and -13 of the three dev families.
    delta = leaky.metric_deltas["leak_rate"]
    assert delta.delta == pytest.approx(6 / 60)
    assert delta.delta_interval is not None and delta.delta_interval[0] > 0
    summary = render_markdown(comparison)
    assert "luna" in summary and "moins chère" in summary


async def test_a_new_critical_error_removes_a_cheaper_candidate() -> None:
    terra = await _report("terra", ScriptedProvider(oracle_patient, model="gpt-5.6-terra"))

    attempts: dict[str, int] = {}

    def inventive(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        run_key = request.context.session_id
        attempts[run_key] = attempts.get(run_key, 0) + 1
        if item_id_of(request) == "atemnot-04" and attempts[run_key] == 1:
            return {"response_kind": "sources", "source_refs": ["fact:fever"]}
        return oracle_patient(request, payload)

    # Leaks nothing and every metric passes after the retry, but it invented a fact.
    luna = await _report("luna", ScriptedProvider(inventive, model="gpt-6-luna"))
    comparison = compare([terra, luna], GATE_RULES)
    assert comparison.recommendation == "terra"
    assert comparison.rejected["luna"][-1].startswith("nouvelle erreur critique")
    assert comparison.candidates[0].new_critical_errors == ["atemnot-04:invented_fact"]


async def test_only_reports_on_the_same_dataset_and_split_compare() -> None:
    luna = await _report("luna", ScriptedProvider(oracle_patient))
    other = luna.model_copy(update={"split": "holdout"})
    with pytest.raises(CompareError):
        compare(
            [
                luna,
                other.model_copy(update={"config": luna.config.model_copy(update={"id": "other"})}),
            ],
            GATE_RULES,
        )
    with pytest.raises(CompareError):
        compare([luna], GATE_RULES)


def test_voice_reports_compare_with_their_own_ratios() -> None:
    baseline = read_report(PROJECT_ROOT / "evals" / "reports" / "examples" / "voice-sim-fake.json")
    candidate = baseline.model_copy(
        update={"config": baseline.config.model_copy(update={"id": "candidate"})}
    )
    comparison = compare([baseline, candidate], GATE_RULES)
    assert "leak_rate" in comparison.configs[0].metrics
    other_case = candidate.model_copy(
        update={
            "dataset": candidate.dataset.model_copy(
                update={"cases": {"ARI-DEV-FR": "5:" + "0" * 64}}
            )
        }
    )
    with pytest.raises(CompareError, match="versions de cas"):
        compare([baseline, other_case], GATE_RULES)
