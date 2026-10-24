"""Promotion gate: the AI fingerprint, the production configurations, the release verdict."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ari.config import PROJECT_ROOT, Settings
from ari.evals.__main__ import main
from ari.evals.configs import load_config
from ari.evals.promotion import (
    AI_VARIABLE,
    COMPOSE_FILE,
    POLICY_FILES,
    PROMOTION_RUNS,
    PROMPT_WIRING,
    PROMPTS_DIR,
    Verdict,
    ai_settings,
    fingerprint,
    production_prompt_ids,
    verdict,
)
from ari.evals.report import Report, read_report

CONFIGS = PROJECT_ROOT / "evals" / "configs"


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A copy of exactly the files the fingerprint reads."""
    root = tmp_path / "checkout"
    shutil.copytree(PROJECT_ROOT / PROMPTS_DIR, root / PROMPTS_DIR)
    for relative in (COMPOSE_FILE, *PROMPT_WIRING, *POLICY_FILES):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PROJECT_ROOT / relative, root / relative)
    return root


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_fingerprint_is_stable_and_ignores_unrelated_files(checkout: Path) -> None:
    before = fingerprint(checkout)
    assert before == fingerprint(checkout) and len(before.sha256) == 64
    (checkout / "README.md").write_text("unrelated", encoding="utf-8")
    _replace(checkout / COMPOSE_FILE, "ARI_TTS_VOICE: ", "ARI_TTS_VOICE:  ")  # Not an AI setting.
    assert fingerprint(checkout).sha256 == before.sha256


@pytest.mark.parametrize(
    ("relative", "old", "new"),
    [
        (f"{PROMPTS_DIR}/patient_v3.txt", "", "Changed. "),  # A prompt.
        (f"{PROMPTS_DIR}/content/segmentation_v1.txt", "", "Changed. "),  # A content prompt.
        (COMPOSE_FILE, "ARI_PATIENT_MODEL:-gpt-6-luna", "ARI_PATIENT_MODEL:-gpt-5.6-terra"),
        (COMPOSE_FILE, "ARI_EXAM_ENGINE:-openai_realtime", "ARI_EXAM_ENGINE:-chain"),
        (PROMPT_WIRING[0], '"patient-v3"', '"patient-v2"'),  # Back to an older prompt file.
        (POLICY_FILES[0], "", "# Changed.\n"),  # The routing policy.
    ],
)
def test_fingerprint_changes_with_prompts_models_and_policy(
    checkout: Path, relative: str, old: str, new: str
) -> None:
    before = fingerprint(checkout).sha256
    path = checkout / relative
    if old:
        _replace(path, old, new)
    else:
        path.write_text(new + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert fingerprint(checkout).sha256 != before


def test_compose_passes_every_ai_setting_so_the_fingerprint_sees_it() -> None:
    names = {
        f"ARI_{name.upper()}"
        for name in Settings.model_fields
        if AI_VARIABLE.match(f"ARI_{name.upper()}")
    }
    assert names and names <= set(ai_settings(PROJECT_ROOT))


def _compose_default(variable: str) -> str:
    value = ai_settings(PROJECT_ROOT)[variable]
    assert value.startswith("${") and ":-" in value
    return value.split(":-", 1)[1].rstrip("}")


def test_promotion_configurations_are_what_production_runs() -> None:
    prompt_ids = production_prompt_ids(PROJECT_ROOT)
    for run in PROMOTION_RUNS:
        config, _ = load_config(CONFIGS / run.config)
        assert config.is_live
        for task, route in config.routes.items():
            assert route.provider == _compose_default(f"ARI_{task.upper()}_PROVIDER")
            assert route.model == _compose_default(f"ARI_{task.upper()}_MODEL")
        # Unset in compose, the exam fact audit follows the patient, as without a route.
        assert "fact_attribution" not in config.routes
        assert _compose_default("ARI_FACT_ATTRIBUTION_MODEL") == ""
        assert set(config.prompts.values()) <= set(prompt_ids)
    engines = {load_config(CONFIGS / run.config)[0].patient_engine for run in PROMOTION_RUNS}
    assert engines == {"training", "exam"}


@pytest.fixture(scope="module")
def gate_report(tmp_path_factory: pytest.TempPathFactory) -> Report:
    out = tmp_path_factory.mktemp("gate") / "report.json"
    argv = ["run", "--suite", "patient-leakage@1", "--config", str(CONFIGS / "fake.yaml")]
    main([*argv, "--split", "holdout", "--gate", "--out", str(out)])
    return read_report(out)


def _as(report: Report, suite: str, config_id: str, *, status: str = "passed") -> Report:
    assert report.gate is not None
    return report.model_copy(
        update={
            "suite": suite,
            "config": report.config.model_copy(update={"id": config_id, "provider": "live"}),
            "gate": report.gate.model_copy(update={"status": status}),
        }
    )


CONFIG_IDS = {run.config: load_config(CONFIGS / run.config)[0].id for run in PROMOTION_RUNS}


def _all_passing(report: Report) -> list[Report]:
    return [_as(report, run.suite, CONFIG_IDS[run.config]) for run in PROMOTION_RUNS]


def test_verdict_passes_only_when_every_promotion_run_passed_its_gate(gate_report: Report) -> None:
    reports = _all_passing(gate_report)
    passed = verdict(reports, CONFIG_IDS, "f" * 64, code="abc")
    assert passed.status == "passed" and passed.fingerprint == "f" * 64
    assert verdict(reports[1:], CONFIG_IDS, "f", code=None).runs[0].status == "missing"
    assert verdict(reports[1:], CONFIG_IDS, "f", code=None).status == "failed"
    failed = [_as(gate_report, reports[0].suite, reports[0].config.id, status="failed")]
    assert verdict([*failed, *reports[1:]], CONFIG_IDS, "f", code=None).status == "failed"


def test_verdict_refuses_fake_dev_and_measurement_reports(gate_report: Report) -> None:
    reports = _all_passing(gate_report)
    fake = reports[0].model_copy(
        update={"config": reports[0].config.model_copy(update={"provider": "fake"})}
    )
    dev = reports[0].model_copy(update={"split": "dev", "gate_mode": False})
    for wrong in (fake, dev):
        result = verdict([wrong, *reports[1:]], CONFIG_IDS, "f", code=None)
        assert result.status == "failed" and result.runs[0].status == "failed"
    assert verdict(reports, CONFIG_IDS, "f", code=None, measurement=True).status == "measurement"


def test_promotion_without_live_calls_no_model_and_fails(tmp_path: Path) -> None:
    assert main(["promotion", "--out-dir", str(tmp_path)]) == 1
    result = Verdict.model_validate(json.loads((tmp_path / "verdict.json").read_text()))
    assert result.status == "failed"
    assert [r.status for r in result.runs] == ["missing"] * len(PROMOTION_RUNS)
    assert result.fingerprint == fingerprint(PROJECT_ROOT).sha256


def test_fingerprint_command_prints_and_writes_the_fingerprint(tmp_path: Path) -> None:
    out = tmp_path / "fingerprint.json"
    assert main(["fingerprint", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["sha256"] == fingerprint(PROJECT_ROOT).sha256
