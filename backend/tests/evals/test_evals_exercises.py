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
from ari.application.exercise_schemas import RUBRIC_CRITERIA, RUBRIC_VERSION
from ari.config import PROJECT_ROOT
from ari.domain.language_exercises import MECHANICAL_CHECKS_VERSION
from ari.evals import gates as gate_rules
from ari.evals.__main__ import main
from ari.evals.exercises.dataset import MECHANICAL_DEFECTS, load_dataset
from ari.evals.exercises.diagnosis import exercise_diagnosis_text
from ari.evals.exercises.gate import GateRefusedError, require_passing_gate
from ari.evals.exercises.runner import ExerciseRunRequest, run_exercises, scorer_version
from ari.evals.manifest import DatasetError, HoldoutAccessError
from ari.evals.report import Report, read_report
from ari.infrastructure.exercises.models import ExerciseConfigError, load_config, load_prompts

CONFIGS = PROJECT_ROOT / "evals" / "configs" / "exercises"
BUILDER = PROJECT_ROOT / "evals" / "datasets" / "build_language_exercises_v1.py"
BUILDER_V2 = PROJECT_ROOT / "evals" / "datasets" / "build_language_exercises_v2.py"
BUILDER_V3 = PROJECT_ROOT / "evals" / "datasets" / "build_language_exercises_v3.py"
BUILDER_V4 = PROJECT_ROOT / "evals" / "datasets" / "build_language_exercises_v4.py"
BUILDER_V5 = PROJECT_ROOT / "evals" / "datasets" / "build_language_exercises_v5.py"
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


def test_the_second_version_of_the_dataset_is_what_its_builder_makes(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("build_language_exercises_v2", BUILDER_V2)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    committed = DATASETS / "language-exercises" / "2"
    for name in ("manifest.json", "dev.jsonl", "holdout.jsonl"):
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes(), name


def test_the_second_version_keeps_every_defect_and_has_more_good_exercises_in_new_families() -> (
    None
):
    v1, v2 = load_dataset(DATASETS, "1"), load_dataset(DATASETS, "2")
    assert v2.ref == "language-exercises@2" and v2.manifest.synthetic
    families_v1 = {f for split in v1.manifest.splits.values() for f in split}
    families_v2 = {f for split in v2.manifest.splits.values() for f in split}
    assert families_v1.isdisjoint(families_v2)  # Nothing of the burned holdout comes back.
    for split in ("dev", "holdout"):
        items = v2.read_split(split, gate=True)  # type: ignore[arg-type]
        assert sum(i.truth.valid for i in items) == 14
        assert {i.truth.defect for i in items} >= {
            "wrong_key",
            "two_correct",
            "no_correct",
            "bad_explanation",
            "wrong_category",
            "level_mismatch",
            "medical_absurd",
        } | MECHANICAL_DEFECTS
    stems = {
        i.candidate.stem
        for split in ("dev", "holdout")
        for i in v2.read_split(split, gate=True)  # type: ignore[arg-type]
    }
    old = {
        i.candidate.stem
        for split in ("dev", "holdout")
        for i in v1.read_split(split, gate=True)  # type: ignore[arg-type]
    }
    assert stems.isdisjoint(old)


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
    assert report.suite == "language-exercises@5"  # The default suite is the latest version.
    assert report.config.provider == "fake" and not report.gate_mode
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


def request(
    provider: Any, *, split: str = "holdout", repetitions: int = 3, version: str = "1"
) -> ExerciseRunRequest:
    config = live_config()
    return ExerciseRunRequest(
        dataset=load_dataset(DATASETS, version),
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


async def test_a_perfect_checker_passes_the_gate_on_the_second_version_too() -> None:
    dataset = load_dataset(DATASETS, "2")
    solver, judge = oracle_for(dataset, "holdout", strict=True)
    provider = ScriptedModels(author=lambda r, p: {}, solver=solver, judge=judge)
    report = await run_exercises(request(provider, version="2"))
    assert report.suite == "language-exercises@2"
    assert report.gate is not None and report.gate.status == "passed", report.gate
    assert report.metrics["false_reject_rate"] == 0.0 and report.critical_errors == []


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


def solving_with_a_twin(keys: dict[str, int]) -> Any:
    def respond(request: Any, payload: dict[str, Any]) -> dict[str, Any]:
        key = keys[request.context.case_hash]
        twin = (key + 1) % len(payload["options"])
        return {
            "chosen_index": key,
            "acceptable_indices": [key, twin],
            "medically_plausible": True,
            "comment": "scripted",
        }

    return respond


async def test_the_diagnosis_says_which_stage_and_which_criterion_refuses_valid_exercises() -> None:
    dataset = load_dataset(DATASETS, "1")
    solver, _ = oracle_for(dataset, "dev", strict=True)
    criterion = RUBRIC_CRITERIA[0]
    provider = ScriptedModels(
        author=lambda r, p: {}, solver=solver, judge=judge_passes(failing=(criterion,))
    )
    report = await run_exercises(request(provider, split="dev", repetitions=1))
    text = exercise_diagnosis_text(report)
    valid = sum(i.truth.valid for i in dataset.read_split("dev", gate=True))
    assert f"{valid} exercice(s) juste(s) refusé(s) sur {valid}" in text
    assert f"Par raison : judge:{criterion} {valid}" in text
    assert f"{criterion} : scripted" in text  # The judge's own reason is shown.
    # A solver that picks another option is told apart from one that sees several answers.
    keys = {
        i.candidate.content_hash: i.candidate.answer_index
        for i in dataset.read_split("dev", gate=True)
    }  # type: ignore[arg-type]

    def another(request: Any, payload: dict[str, Any]) -> dict[str, Any]:
        chosen = (keys[request.context.case_hash] + 1) % len(payload["options"])
        return {
            "chosen_index": chosen,
            "acceptable_indices": [chosen],
            "medically_plausible": True,
            "comment": "scripted",
        }

    other = ScriptedModels(author=lambda r, p: {}, solver=another, judge=judge_passes())
    assert "solver:chose_another_option" in exercise_diagnosis_text(
        await run_exercises(request(other, split="dev", repetitions=1))
    )
    several = ScriptedModels(
        author=lambda r, p: {}, solver=solve_as(also=[1]), judge=judge_passes()
    )
    assert "solver:sees_several_acceptable_options" in exercise_diagnosis_text(
        await run_exercises(request(several, split="dev", repetitions=1))
    )
    plain = ScriptedModels(author=lambda r, p: {}, solver=solve_as(), judge=judge_passes())
    holdout = await run_exercises(request(plain))
    assert "réservé au split dev" in exercise_diagnosis_text(holdout)


async def test_the_diagnosis_leaves_provider_failures_out_of_the_refusals() -> None:
    dataset = load_dataset(DATASETS, "1")
    solver, _ = oracle_for(dataset, "dev", strict=True)
    provider = ScriptedModels(
        author=lambda r, p: {}, solver=solver, judge=judge_passes(failing=(RUBRIC_CRITERIA[0],))
    )
    report = await run_exercises(request(provider, split="dev", repetitions=1))
    valid_ids = {i.id for i in dataset.read_split("dev", gate=True) if i.truth.valid}  # type: ignore[arg-type]
    broken = next(e for e in report.items if e.item_id in valid_ids)
    failed = broken.runs[0].model_copy(update={"usable": False, "errors": ["boom"]})
    items = [
        e.model_copy(update={"runs": [failed]}) if e.item_id == broken.item_id else e
        for e in report.items
    ]
    text = exercise_diagnosis_text(report.model_copy(update={"items": items}))
    assert f"{len(valid_ids) - 1} exercice(s) juste(s) refusé(s) sur {len(valid_ids) - 1}" in text
    assert "sans réponse exploitable (hors décompte) : 1, 1 x boom" in text


async def test_a_report_without_the_key_is_completed_from_the_dataset_not_guessed() -> None:
    dataset = load_dataset(DATASETS, "1")
    keys = {
        i.candidate.content_hash: i.candidate.answer_index
        for i in dataset.read_split("dev", gate=True)  # type: ignore[arg-type]
    }
    provider = ScriptedModels(
        author=lambda r, p: {}, solver=solving_with_a_twin(keys), judge=judge_passes()
    )
    report = await run_exercises(request(provider, split="dev", repetitions=1))

    def without_key(entry: Any) -> Any:  # What a report written before the key was recorded holds.
        runs = []
        for run in entry.runs:
            detail = {k: v for k, v in (run.detail or {}).items() if k not in {"key_index", "stem"}}
            runs.append(run.model_copy(update={"detail": detail}))
        return entry.model_copy(update={"runs": runs})

    old = report.model_copy(update={"items": [without_key(e) for e in report.items]})
    assert "solver:cause_unknown_without_the_key" in exercise_diagnosis_text(old)
    by_id = {i.id: i for i in dataset.read_split("dev", gate=True)}  # type: ignore[arg-type]
    completed = exercise_diagnosis_text(old, by_id)
    assert "solver:sees_several_acceptable_options" in completed
    assert "cause_unknown" not in completed


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
    other = batch.model_copy(update={"rubric_version": "exercise-rubric-v1"})
    refused(passing_report, other, "grille ou contrôles")
    changed_prompt = batch.model_copy(
        update={"judge": batch.judge.model_copy(update={"prompt_hash": "a" * 64})}
    )
    refused(passing_report, changed_prompt, "prompt exercise-judge-v3")
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


def test_each_route_keeps_its_own_anthropic_settings() -> None:
    from types import SimpleNamespace

    from ari.infrastructure.exercises.models import ExerciseModelsConfig, _RoutedProvider

    config = ExerciseModelsConfig.model_validate(
        {
            "id": "two-anthropic",
            "description": "two routes of one vendor",
            "provider": "live",
            "routes": {
                "author": {
                    "provider": "anthropic",
                    "model": "claude-haiku-5-5",
                    "max_tokens": 1024,
                    "effort": "low",
                },
                "checker": {
                    "provider": "anthropic",
                    "model": "claude-sonnet-5-5",
                    "max_tokens": 16000,
                    "effort": "high",
                },
            },
            "prompts": {
                "author": "exercise-author-v1",
                "solver": "exercise-solver-v2",
                "judge": "exercise-judge-v3",
            },
        }
    )
    settings = SimpleNamespace(openai_api_key=None, anthropic_api_key="test-key")
    backends = _RoutedProvider(config, settings)._backends  # type: ignore[arg-type]
    assert (backends["author"]._max_tokens, backends["author"]._effort) == (1024, "low")  # type: ignore[union-attr]
    assert (backends["checker"]._max_tokens, backends["checker"]._effort) == (16000, "high")  # type: ignore[union-attr]


def test_the_double_agreement_configuration_differs_from_the_single_one_by_its_passes_only() -> (
    None
):
    single, single_sha = load_config(CONFIGS / "luna-sonnet.yaml")
    double, double_sha = load_config(CONFIGS / "luna-sonnet-double.yaml")
    assert (single.check_passes, double.check_passes) == (1, 2)
    assert single.routes == double.routes and single.prompts == double.prompts
    assert single_sha != double_sha  # A gate report for one never backs a batch of the other.


def test_the_third_version_of_the_dataset_is_what_its_builder_makes(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("build_language_exercises_v3", BUILDER_V3)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    committed = DATASETS / "language-exercises" / "3"
    for name in ("manifest.json", "dev.jsonl", "holdout.jsonl"):
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes(), name


def test_the_third_version_adds_the_near_miss_defect_and_a_holdout_of_new_families() -> None:
    v2, v3 = load_dataset(DATASETS, "2"), load_dataset(DATASETS, "3")
    assert v3.ref == "language-exercises@3" and v3.manifest.synthetic
    dev = v3.read_split("dev", gate=True)  # type: ignore[arg-type]
    holdout = v3.read_split("holdout", gate=True)  # type: ignore[arg-type]
    families_v2 = {f for split in v2.manifest.splits.values() for f in split}
    # The development split is the whole of version 2, the holdout is made of new families.
    assert {i.family for i in dev} >= {f for f in families_v2 if not f.endswith("absurd")}
    assert {i.family for i in holdout}.isdisjoint(families_v2)
    assert {i.family for i in dev}.isdisjoint({i.family for i in holdout})
    stems = {i.candidate.stem for i in dev}
    assert stems.isdisjoint({i.candidate.stem for i in holdout})
    # The regression that failed the version 2 holdout is kept, with the typo as its key.
    regression = next(i for i in dev if i.id == "e-h05-choice-cloze:near_miss_key")
    assert not regression.truth.valid and regression.truth.defect == "near_miss_key"
    assert regression.candidate.options[regression.candidate.answer_index] == "angeschlagenn"
    for split in (dev, holdout):
        assert any(i.truth.defect == "near_miss_key" for i in split)
        assert all(i.truth.defect != "near_miss_key" for i in split if i.truth.valid)
    assert sum(i.truth.valid for i in holdout) == 14
    # A near miss is the good exercise with a different key and nothing else changed.
    valid = {i.family: i.candidate for i in dev + holdout if i.truth.valid}
    for item in dev + holdout:
        if item.truth.defect != "near_miss_key":
            continue
        good = valid[item.family]
        kept = good.options[good.answer_index]
        assert item.candidate.options[item.candidate.answer_index] != kept
        assert item.candidate.stem == good.stem and item.candidate.category == good.category
        assert item.candidate.level == good.level


async def test_a_perfect_checker_passes_the_gate_on_the_third_version() -> None:
    dataset = load_dataset(DATASETS, "3")
    solver, judge = oracle_for(dataset, "holdout", strict=True)
    provider = ScriptedModels(author=lambda r, p: {}, solver=solver, judge=judge)
    report = await run_exercises(request(provider, version="3"))
    assert report.suite == "language-exercises@3"
    assert report.gate is not None and report.gate.status == "passed", report.gate
    assert report.metrics["false_accept_rate.near_miss_key"] == 0.0
    assert report.metrics["false_reject_rate"] == 0.0 and report.critical_errors == []


def test_the_diagnosis_names_the_new_refusal_reasons() -> None:
    from ari.evals.exercises.diagnosis import rejection_reasons

    none = {
        "stage": "blind_solver",
        "key_index": 1,
        "solver": {"chosen_index": -1, "acceptable_indices": [], "medically_plausible": True},
    }
    assert rejection_reasons(none) == ["solver:found_no_correct_option"]
    # A refusal of a later pass sees the options in another order: only the criterion is told.
    second = {"stage": "rubric_judge", "issues": ["key_form_exact", "pass_2"]}
    assert rejection_reasons(second) == ["pass_2:judge:key_form_exact"]
    second_solver = {"stage": "blind_solver", "issues": ["pass_2"]}
    assert rejection_reasons(second_solver) == ["pass_2:solver"]


def test_the_fourth_version_of_the_dataset_is_what_its_builder_makes(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("build_language_exercises_v4", BUILDER_V4)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    committed = DATASETS / "language-exercises" / "4"
    for name in ("manifest.json", "dev.jsonl", "holdout.jsonl"):
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes(), name


def test_the_fourth_version_reads_everything_measured_as_dev_and_has_a_new_holdout() -> None:
    v3, v4 = load_dataset(DATASETS, "3"), load_dataset(DATASETS, "4")
    assert v4.ref == "language-exercises@4" and v4.manifest.synthetic
    dev = v4.read_split("dev", gate=True)  # type: ignore[arg-type]
    holdout = v4.read_split("holdout", gate=True)  # type: ignore[arg-type]
    families_v3 = {f for split in v3.manifest.splits.values() for f in split}
    # Both splits of version 3 are development data now: the regression and the burnt holdout.
    assert {i.family for i in dev} >= {f for f in families_v3 if not f.endswith("absurd")}
    assert {i.family for i in holdout}.isdisjoint(families_v3)
    assert {i.candidate.stem for i in holdout}.isdisjoint({i.candidate.stem for i in dev})
    assert any(i.id == "e-h05-choice-cloze:near_miss_key" for i in dev)
    assert sum(i.truth.valid for i in holdout) == 14
    defects = {i.truth.defect for i in holdout}
    assert defects >= {"wrong_key", "two_correct", "no_correct", "near_miss_key", "bad_explanation"}
    assert defects >= {"wrong_category", "level_mismatch", "medical_absurd"} | MECHANICAL_DEFECTS


def test_the_fourth_version_leaves_the_model_defects_to_the_models() -> None:
    from ari.domain.language_exercises import mechanical_issues

    dataset = load_dataset(DATASETS, "4")
    for split in ("dev", "holdout"):
        for item in dataset.read_split(split, gate=True):  # type: ignore[arg-type]
            issues = mechanical_issues(item.candidate)
            if item.truth.defect in MECHANICAL_DEFECTS:
                assert issues, item.id  # The code must refuse these before any call.
            else:
                assert not issues, (item.id, issues)  # A model has to be the one to refuse these.


def test_a_vocabulary_exercise_does_not_use_its_typo_as_its_wrong_answer() -> None:
    """The `no_correct` and `near_miss_key` of a family are two different faults."""
    dataset = load_dataset(DATASETS, "4")
    items = {i.id: i for i in dataset.read_split("holdout", gate=True)}  # type: ignore[arg-type]
    family = "e-m12-choice-cloze"
    no_correct, near_miss = items[f"{family}:no_correct"], items[f"{family}:near_miss_key"]
    assert no_correct.candidate.key == "fotokopiert"
    assert near_miss.candidate.key == "hervorgehobn"


async def test_a_perfect_checker_passes_the_gate_on_the_fourth_version() -> None:
    dataset = load_dataset(DATASETS, "4")
    solver, judge = oracle_for(dataset, "holdout", strict=True)
    provider = ScriptedModels(author=lambda r, p: {}, solver=solver, judge=judge)
    report = await run_exercises(request(provider, version="4"))
    assert report.suite == "language-exercises@4"
    assert report.gate is not None and report.gate.status == "passed", report.gate
    assert report.metrics["false_reject_rate"] == 0.0 and report.critical_errors == []


def test_the_fifth_version_of_the_dataset_is_what_its_builder_makes(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("build_language_exercises_v5", BUILDER_V5)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    committed = DATASETS / "language-exercises" / "5"
    for name in ("manifest.json", "dev.jsonl", "holdout.jsonl"):
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes(), name


def test_the_fifth_version_drops_two_doubtful_families_and_has_a_new_holdout() -> None:
    v4, v5 = load_dataset(DATASETS, "4"), load_dataset(DATASETS, "5")
    assert v5.ref == "language-exercises@5" and v5.manifest.synthetic
    dev = v5.read_split("dev", gate=True)  # type: ignore[arg-type]
    holdout = v5.read_split("holdout", gate=True)  # type: ignore[arg-type]
    families_v4 = {f for split in v4.manifest.splits.values() for f in split}
    dropped = {"e-k12-choice-cloze", "e-k13-order-correction"}
    # Everything of version 4 that was measured is development data, except the dropped families.
    assert {i.family for i in dev} == families_v4 - dropped
    assert {i.family for i in holdout}.isdisjoint(families_v4)
    assert {i.candidate.stem for i in holdout}.isdisjoint({i.candidate.stem for i in dev})
    assert {i.candidate.correct_sentence() for i in holdout}.isdisjoint(
        {i.candidate.correct_sentence() for i in dev}
    )
    assert sum(i.truth.valid for i in holdout) == 14
    defects = {i.truth.defect for i in holdout}
    assert defects >= {"wrong_key", "two_correct", "no_correct", "near_miss_key", "bad_explanation"}
    assert defects >= {"wrong_category", "level_mismatch", "medical_absurd"} | MECHANICAL_DEFECTS
    assert {i.candidate.level for i in holdout if i.truth.valid} == {"B1", "B2", "C1"}
    assert "dropped" in " ".join(v5.manifest.limitations).lower() or any(
        "e-k12" in text and "e-k13" in text for text in v5.manifest.limitations
    )


def test_the_fifth_version_leaves_the_model_defects_to_the_models() -> None:
    from ari.domain.language_exercises import mechanical_issues

    dataset = load_dataset(DATASETS, "5")
    for split in ("dev", "holdout"):
        for item in dataset.read_split(split, gate=True):  # type: ignore[arg-type]
            issues = mechanical_issues(item.candidate)
            if item.truth.defect in MECHANICAL_DEFECTS:
                assert issues, item.id
            else:
                assert not issues, (item.id, issues)


def test_every_near_miss_of_the_fifth_version_is_a_small_typo_of_the_key() -> None:
    from difflib import SequenceMatcher

    dataset = load_dataset(DATASETS, "5")
    items = dataset.read_split("holdout", gate=True)  # type: ignore[arg-type]
    by_id = {i.id: i for i in items}
    near = [i for i in items if i.truth.defect == "near_miss_key"]
    assert len(near) == 12
    for item in near:
        valid = by_id[item.family + ":valid"]
        assert item.candidate.key != valid.candidate.key
        ratio = SequenceMatcher(None, item.candidate.key, valid.candidate.key).ratio()
        assert ratio >= 0.6, (item.id, ratio)  # A transposition of "das" gives 0.67.


async def test_a_perfect_checker_passes_the_gate_on_the_fifth_version() -> None:
    dataset = load_dataset(DATASETS, "5")
    solver, judge = oracle_for(dataset, "holdout", strict=True)
    provider = ScriptedModels(author=lambda r, p: {}, solver=solver, judge=judge)
    report = await run_exercises(request(provider, version="5"))
    assert report.suite == "language-exercises@5"
    assert report.gate is not None and report.gate.status == "passed", report.gate
    assert report.metrics["false_reject_rate"] == 0.0 and report.critical_errors == []


def test_a_swapped_level_is_judged_against_the_true_level_within_one_step() -> None:
    from ari.application.services.exercise_bank import CheckOutcome
    from ari.evals.exercises.runner import score

    dev = load_dataset(DATASETS, "4").read_split("dev", gate=False)
    valid = next(i for i in dev if i.truth.valid)
    moved = "B2" if valid.candidate.level != "B2" else "C1"
    counts, outcomes, _ = score(valid, CheckOutcome(True, "accepted", level=moved))
    assert counts["valid.relabeled"] == 1.0 and outcomes == []
    counts, _, _ = score(valid, CheckOutcome(True, "accepted", level=valid.candidate.level))
    assert "valid.relabeled" not in counts
    # A swapped level (B1 <-> C1) that comes out accepted is a false acceptance when it is stored
    # more than one step from the true level; one step is the imprecision a valid item has too.
    mismatch = next(i for i in dev if i.truth.defect == "level_mismatch")
    true_level = next(
        i for i in dev if i.family == mismatch.family and i.truth.valid
    ).candidate.level
    assert true_level != mismatch.candidate.level
    counts, outcomes, _ = score(mismatch, CheckOutcome(True, "accepted", level="B2"), true_level)
    assert outcomes == [] and "defective.accepted" not in counts
    assert counts["level_swap.kept_within_one_step"] == 1.0
    assert counts["defect.level_mismatch.total"] == 1.0
    # Stored under the swapped (declared) level, two steps from the truth: a false acceptance.
    counts, outcomes, _ = score(
        mismatch, CheckOutcome(True, "accepted", level=mismatch.candidate.level), true_level
    )
    assert outcomes == ["false_accept"] and counts["defect.level_mismatch.accepted"] == 1.0
    # Without a true level the measure stays strict.
    counts, outcomes, _ = score(mismatch, CheckOutcome(True, "accepted", level="B2"))
    assert outcomes == ["false_accept"]
    # Refused stays refused.
    counts, outcomes, _ = score(
        mismatch, CheckOutcome(False, "rubric_judge", ["level_fit"]), true_level
    )
    assert outcomes == [] and "defective.accepted" not in counts


def test_the_scorer_names_the_relabelling_and_the_judge_prompt_is_the_third_version() -> None:
    assert scorer_version().startswith("language-exercises-scorer-v3+exercise-rubric-v3+")
    assert load_config(CONFIGS / "luna-sonnet.yaml")[0].prompts.judge == "exercise-judge-v3"


def test_the_diagnosis_says_which_level_the_judge_read_when_it_refuses_for_two_steps() -> None:
    from ari.evals.exercises.diagnosis import _explanation

    text = _explanation(
        {"issues": ["level_fit"], "level": "B1", "judge": {"estimated_level": "C1", "verdicts": []}}
    )
    assert text == "level_fit : niveau lu C1 pour B1"


def test_the_haiku_configuration_differs_from_the_sonnet_one_by_the_checking_model_only() -> None:
    sonnet, sonnet_sha = load_config(CONFIGS / "luna-sonnet.yaml")
    haiku, haiku_sha = load_config(CONFIGS / "luna-haiku.yaml")
    assert haiku.routes["author"] == sonnet.routes["author"]
    assert (haiku.routes["checker"].provider, haiku.routes["checker"].model) == (
        "anthropic",
        "claude-haiku-5-5",
    )
    assert haiku.routes["checker"].max_tokens == sonnet.routes["checker"].max_tokens
    assert haiku.prompts == sonnet.prompts and haiku.check_passes == sonnet.check_passes == 1
    assert haiku_sha != sonnet_sha  # A gate report for one never backs a batch of the other.
    assert haiku.author_identity != ("anthropic", "claude-haiku-5-5")
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "evals-exercises.yml").read_text()
    assert "luna-haiku" in workflow and "luna-sonnet" not in workflow
    # Repetitions can be asked for the dev split only: the holdout keeps those of gates.yaml.
    assert 'options: ["1", "3"]' in workflow
    assert 'if [ "$SPLIT" = holdout ]; then args+=(--gate); else args+=(--repeat' in workflow


def test_the_diagnosis_shows_the_level_read_of_a_swap_and_counts_the_tolerated_ones() -> None:
    from ari.evals.exercises.diagnosis import _level_read

    detail = {
        "truth": {"defect": "level_mismatch"},
        "level": "B1",
        "true_level": "C1",
        "judge": {"estimated_level": "B1"},
    }
    assert _level_read(detail) == " [annoncé B1, vrai C1, lu B1]"
    assert _level_read({"truth": {"defect": "wrong_key"}}) == ""
