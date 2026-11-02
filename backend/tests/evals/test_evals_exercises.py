"""`language-exercises@1`: the dataset, the scoring, the gate, and the gate in front of the bank."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from exercise_scripts import (
    CHECKER,
    ScriptedModels,
    judge_passes,
    live_config,
    oracle_for,
    passing_gate_report,
    solve_as,
)
from scripted import DATASETS, GATES, PRICING

from ari.application.exercise_batch import ExerciseBatch, StageInfo
from ari.application.exercise_schemas import RUBRIC_VERSION
from ari.config import PROJECT_ROOT
from ari.domain.language_exercises import MECHANICAL_CHECKS_VERSION
from ari.evals import gates as gate_rules
from ari.evals.__main__ import main
from ari.evals.exercises.dataset import MECHANICAL_DEFECTS, load_dataset
from ari.evals.exercises.gate import GateRefusedError, require_passing_gate
from ari.evals.exercises.runner import ExerciseRunRequest, run_exercises, scorer_version
from ari.evals.manifest import DatasetError, HoldoutAccessError
from ari.evals.report import Report, read_report
from ari.infrastructure.exercises.models import ExerciseConfigError, load_config, load_prompts

CONFIGS = PROJECT_ROOT / "evals" / "configs" / "exercises"
BUILDER = PROJECT_ROOT / "evals" / "datasets" / "build_language_exercises_v1.py"
SUITE = "language-exercises@1"


def test_the_committed_dataset_is_what_the_builder_makes(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("build_language_exercises_v1", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    committed = DATASETS / "language-exercises" / "1"
    for name in ("manifest.json", "dev.jsonl", "holdout.jsonl"):
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes(), name


def test_the_dataset_is_split_by_family_and_every_defect_is_present_in_both_splits() -> None:
    dataset = load_dataset(DATASETS, "1")
    assert dataset.ref == SUITE and dataset.manifest.synthetic
    dev, holdout = (dataset.read_split(s, gate=True) for s in ("dev", "holdout"))  # type: ignore[arg-type]
    assert {i.family for i in dev}.isdisjoint({i.family for i in holdout})
    for split in (dev, holdout):
        defects = {i.truth.defect for i in split}
        assert (
            defects
            >= {
                "wrong_key",
                "two_correct",
                "no_correct",
                "bad_explanation",
                "wrong_category",
                "level_mismatch",
                "medical_absurd",
            }
            | MECHANICAL_DEFECTS
        )
        assert sum(i.truth.valid for i in split) >= 8


def test_the_holdout_is_only_read_in_gate_mode() -> None:
    with pytest.raises(HoldoutAccessError):
        load_dataset(DATASETS, "1").read_split("holdout", gate=False)


def test_a_dataset_file_that_changed_stops_the_run(tmp_path: Path) -> None:
    root = tmp_path / "language-exercises" / "1"
    root.mkdir(parents=True)
    for name in ("manifest.json", "dev.jsonl", "holdout.jsonl"):
        (root / name).write_bytes((DATASETS / "language-exercises" / "1" / name).read_bytes())
    (root / "dev.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="empreinte"):
        load_dataset(tmp_path, "1")


def test_the_fake_configuration_runs_the_chain_without_a_key_and_measures_nothing(
    tmp_path: Path,
) -> None:
    out = tmp_path / "report.json"
    assert main(["exercises", "--config", str(CONFIGS / "fake.yaml"), "--out", str(out)]) == 0
    report = read_report(out)
    assert report.suite == SUITE and report.config.provider == "fake" and not report.gate_mode
    assert report.metrics["mechanical_catch_rate"] == 1.0
    assert report.metrics["structured_after_retry"] == 1.0
    assert report.versions.scorer == scorer_version()
    assert set(report.versions.prompts) == {"solver", "judge"}


def test_real_models_are_never_called_without_live() -> None:
    config, _ = load_config(CONFIGS / "luna-sonnet.yaml")
    assert config.is_live and config.routes["author"].identity != config.routes["checker"].identity
    code = main(["exercises", "--config", str(CONFIGS / "luna-sonnet.yaml"), "--repeat", "1"])
    assert code == 2  # "--live" is required: the run is billed.


def test_the_holdout_needs_the_gate_flag() -> None:
    argv = ["exercises", "--config", str(CONFIGS / "fake.yaml"), "--split", "holdout"]
    assert main(argv) == 2


def test_the_production_like_configuration_is_valid_and_its_prompts_exist() -> None:
    config, _ = load_config(CONFIGS / "luna-sonnet.yaml")
    prompts = load_prompts(config)
    assert set(prompts) == {"author", "solver", "judge"}
    with pytest.raises(ExerciseConfigError):
        load_config(PROJECT_ROOT / "evals" / "gates.yaml")


def request(provider: Any, *, split: str = "holdout", repetitions: int = 3) -> ExerciseRunRequest:
    config = live_config()
    return ExerciseRunRequest(
        dataset=load_dataset(DATASETS, "1"),
        split=split,  # type: ignore[arg-type]
        gate_mode=split == "holdout",
        config=config,
        config_sha256="0" * 64,
        provider=provider,
        prompts=load_prompts(config),
        pricing=PRICING,
        repetitions=repetitions,
        gates=gate_rules.load_gates(GATES)[0],
    )


async def test_a_perfect_checker_passes_the_gate_with_nothing_wrong_accepted() -> None:
    dataset = load_dataset(DATASETS, "1")
    solver, judge = oracle_for(dataset, "holdout", strict=True)
    provider = ScriptedModels(author=lambda r, p: {}, solver=solver, judge=judge)
    report = await run_exercises(request(provider))
    assert report.gate is not None and report.gate.status == "passed", report.gate
    assert report.metrics["false_accept_rate"] == 0.0 and report.metrics["false_reject_rate"] == 0.0
    assert report.metrics["mechanical_catch_rate"] == 1.0
    assert report.critical_errors == [] and report.repetitions == 3
    assert all(run.detail is None for entry in report.items for run in entry.runs)  # Holdout.
    assert report.cost.total_micro_eur is not None and report.cost.total_micro_eur > 0
    # The mechanical defects cost no call: only the other items reach a model.
    assert report.metrics["false_accept_rate.bad_format"] == 0.0


async def test_a_checker_that_accepts_everything_fails_the_gate_with_critical_errors() -> None:
    provider = ScriptedModels(author=lambda r, p: {}, solver=solve_as(), judge=judge_passes())
    # solve_as() answers option 0 with no second option: it finds the key whenever it is first.
    report = await run_exercises(request(provider, split="dev", repetitions=1))
    assert (
        report.metrics["false_accept_rate"] is not None and report.metrics["false_accept_rate"] > 0
    )
    assert report.metrics["mechanical_catch_rate"] == 1.0
    assert any(c.endswith(":false_accept") for c in report.critical_errors)
    gated = await run_exercises(request(provider))
    assert gated.gate is not None and gated.gate.status == "failed"
    assert any(c.endswith(":false_accept") for c in gated.critical_errors)
    assert {c.metric for c in gated.gate.checks if c.status == "failed"} >= {"false_accept_rate"}
    assert gated.gate.new_critical_errors


async def test_a_gate_run_repeats_each_item_at_least_three_times() -> None:
    provider = ScriptedModels(author=lambda r, p: {}, solver=solve_as(), judge=judge_passes())
    report = await run_exercises(request(provider, repetitions=2))
    assert report.gate is not None
    assert {c.metric: c.status for c in report.gate.checks}["repetitions"] == "failed"


# ----- the gate in front of the bank -------------------------------------------------------


def stage(name: str, prompt: Any, model: tuple[str, str]) -> StageInfo:
    return StageInfo.model_validate(
        {
            "stage": name,
            "provider": model[0],
            "model": model[1],
            "model_returned": None,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.content_hash,
        }
    )


def batch_for_gate() -> ExerciseBatch:
    prompts = load_prompts(live_config())
    return ExerciseBatch(
        batch_id="b",
        mechanical_checks_version=MECHANICAL_CHECKS_VERSION,
        config_sha256="0" * 64,  # The one the scripted gate report carries.
        author=stage("author", prompts["author"], ("openai", "gpt-6-luna")),
        solver=stage("blind_solver", prompts["solver"], CHECKER),
        judge=stage("rubric_judge", prompts["judge"], CHECKER),
        specs=[],
        accepted=[],
        rejected=[],
        calls=[],
    )


@pytest.fixture(scope="module")
def passing_report() -> Report:
    return passing_gate_report()


def test_a_batch_made_with_what_was_measured_is_let_in(passing_report: Report) -> None:
    require_passing_gate(passing_report, batch_for_gate())


def refused(report: Report, batch: ExerciseBatch, match: str) -> None:
    with pytest.raises(GateRefusedError, match=match):
        require_passing_gate(report, batch)


def test_each_way_a_report_can_fail_to_back_a_batch(passing_report: Report) -> None:
    batch = batch_for_gate()
    config = passing_report.config
    gate = passing_report.gate
    assert gate is not None
    refused(
        passing_report.model_copy(
            update={"config": config.model_copy(update={"provider": "fake"})}
        ),
        batch,
        "vrais modèles",
    )
    refused(passing_report.model_copy(update={"split": "dev"}), batch, "holdout")
    refused(passing_report.model_copy(update={"gate_mode": False}), batch, "holdout")
    refused(
        passing_report.model_copy(update={"gate": gate.model_copy(update={"status": "failed"})}),
        batch,
        "pas passé",
    )
    refused(passing_report.model_copy(update={"gate": None}), batch, "pas passé")
    refused(passing_report.model_copy(update={"suite": "patient-leakage@1"}), batch, "pas de")
    refused(
        passing_report,
        batch.model_copy(update={"config_sha256": "1" * 64}),
        "fichier de configuration",
    )
    refused(
        passing_report, batch.model_copy(update={"config_sha256": None}), "fichier de configuration"
    )
    other = batch.model_copy(update={"rubric_version": "exercise-rubric-v2"})
    refused(passing_report, other, "grille ou contrôles")
    changed_prompt = batch.model_copy(
        update={"judge": batch.judge.model_copy(update={"prompt_hash": "a" * 64})}
    )
    refused(passing_report, changed_prompt, "prompt exercise-judge-v1")
    other_model = batch.model_copy(
        update={"solver": batch.solver.model_copy(update={"model": "claude-opus-5-5"})}
    )
    refused(passing_report, other_model, "pas le modèle qui a été mesuré")
    assert scorer_version().endswith(f"{RUBRIC_VERSION}+{MECHANICAL_CHECKS_VERSION}")


def test_the_gates_file_has_thresholds_for_the_suite() -> None:
    gates, _ = gate_rules.load_gates(GATES)
    suite = gates.suites[SUITE]
    thresholds = {t.metric: t for t in suite.thresholds}
    assert thresholds["false_accept_rate"].max == 0.0
    assert suite.critical_outcomes == ["false_accept"]
    assert json.dumps(gates.version).startswith('"gates-v')
