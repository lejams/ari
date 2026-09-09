from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from ari.benchmarks.runner import (
    LiveBudget,
    compare_champion_challenger,
    run_offline_benchmark,
    validate_live_guard,
    write_reports,
)
from ari.benchmarks.schemas import load_rules, load_suite
from ari.benchmarks.voice import main

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUITE_PATH = PROJECT_ROOT / "benchmarks" / "voice" / "german_medical_smoke.v1.yaml"
RULES_PATH = PROJECT_ROOT / "benchmarks" / "voice" / "champion_challenger.v1.yaml"


def test_corpus_schema_is_versioned_bilingual_and_synthetic() -> None:
    suite = load_suite(SUITE_PATH)

    assert suite.schema_version == "voice-benchmark-suite-v1"
    assert suite.language == "de-DE"
    assert suite.translation_language == "fr-FR"
    assert all(scenario.translation_fr for scenario in suite.scenarios)
    assert all(scenario.origin_status == "synthetic" for scenario in suite.scenarios)
    assert all(scenario.audio is None for scenario in suite.scenarios)


def test_offline_benchmark_generates_json_french_report_and_manifest_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("offline benchmark attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    monkeypatch.setattr(socket.socket, "connect_ex", fail_network)
    suite = load_suite(SUITE_PATH)
    rules = load_rules(RULES_PATH)
    stacks = tuple(sorted(suite.offline_stack_fixtures))
    report = run_offline_benchmark(
        suite=suite,
        rules=rules,
        stack_ids=stacks,
        repetitions=1,
        seed=7,
    )
    paths = write_reports(
        report,
        output_directory=tmp_path,
        suite_path=SUITE_PATH,
        rules_path=RULES_PATH,
        project_root=PROJECT_ROOT,
    )

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    markdown = paths["markdown"].read_text(encoding="utf-8")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert payload["schema_version"] == "voice-benchmark-report-v1"
    assert payload["conclusion"] == "insufficient_data"
    assert {item["stack_id"] for item in payload["stacks"]} == set(stacks)
    assert "Rapport benchmark vocal ARI" in markdown
    assert "fixtures sont synthétiques" in markdown
    assert "Comparaisons champion/challenger" in markdown
    assert "Échecs d'invariants patient" in markdown
    assert manifest["schema_version"] == "voice-benchmark-manifest-v1"
    assert len(manifest["suite_sha256"]) == 64
    assert len(manifest["rules_sha256"]) == 64
    assert "worktree_dirty" in manifest


def _eligible_summary(stack_id: str, cost: float, human: float) -> dict[str, object]:
    return {
        "stack_id": stack_id,
        "conclusion": "eligible",
        "total_cost_usd": cost,
        "human_quality_score": human,
    }


def test_champion_challenger_requires_cost_reduction_and_human_quality() -> None:
    rules = load_rules(RULES_PATH)
    comparison = compare_champion_challenger(
        _eligible_summary("champion", 1.0, 4.5),
        _eligible_summary("challenger", 0.6, 4.2),
        rules,
    )

    assert comparison["conclusion"] == "eligible"
    assert comparison["recommended_replacement"] is True


def test_missing_data_can_never_promote_a_challenger() -> None:
    rules = load_rules(RULES_PATH)
    challenger = _eligible_summary("challenger", 0.5, 4.4)
    challenger["conclusion"] = "insufficient_data"

    comparison = compare_champion_challenger(
        _eligible_summary("champion", 1.0, 4.5), challenger, rules
    )

    assert comparison["conclusion"] == "insufficient_data"
    assert comparison["recommended_replacement"] is False


def test_live_budget_checks_ceiling_before_each_call() -> None:
    budget = LiveBudget(0.10).reserve(0.06)

    with pytest.raises(RuntimeError, match="ceiling"):
        budget.reserve(0.05)
    with pytest.raises(RuntimeError, match="conservative"):
        LiveBudget(1.0).reserve(None)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 0.0])
def test_live_budget_rejects_nonfinite_or_nonpositive_ceiling(value: float) -> None:
    with pytest.raises(ValueError, match="finite and strictly positive"):
        LiveBudget(value)


def test_live_preparation_only_validates_guards_without_producing_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ARI_BENCHMARK_LIVE_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-placeholder")
    assert main([
        "--suite", str(SUITE_PATH), "--mode", "live", "--max-cost-usd", "1",
        "--confirm-live", "--validate-live-guard-only", "--output-dir", str(tmp_path),
    ]) == 0
    assert "aucun appel fournisseur" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_live_mode_refuses_without_every_explicit_activation() -> None:
    with pytest.raises(RuntimeError, match="ARI_BENCHMARK_LIVE_ENABLED"):
        validate_live_guard(
            mode="live",
            maximum_cost_usd=1.0,
            confirmed=True,
            environment={},
        )
    with pytest.raises(RuntimeError, match="provider key"):
        validate_live_guard(
            mode="live",
            maximum_cost_usd=1.0,
            confirmed=True,
            environment={"ARI_BENCHMARK_LIVE_ENABLED": "true"},
        )
    with pytest.raises(RuntimeError, match="strictly positive"):
        validate_live_guard(
            mode="live",
            maximum_cost_usd=0,
            confirmed=True,
            environment={
                "ARI_BENCHMARK_LIVE_ENABLED": "true",
                "OPENAI_API_KEY": "test-only-placeholder",
            },
        )
    with pytest.raises(RuntimeError, match="explicit --confirm-live"):
        validate_live_guard(
            mode="live",
            maximum_cost_usd=1.0,
            confirmed=False,
            environment={
                "ARI_BENCHMARK_LIVE_ENABLED": "true",
                "OPENAI_API_KEY": "test-only-placeholder",
            },
        )
