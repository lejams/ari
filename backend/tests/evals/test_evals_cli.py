"""`python -m ari.evals`: fake runs end to end; real models never called without --live."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripted import DATASETS, PRICING, PROMPTS

from ari.config import PROJECT_ROOT
from ari.evals.__main__ import main
from ari.evals.configs import ConfigError, EvalConfig, build_provider, load_config, load_prompts
from ari.evals.report import read_report

CONFIGS = PROJECT_ROOT / "evals" / "configs"


@pytest.mark.parametrize(
    "suite", ["patient-leakage@1", "evaluation-language@1", "evaluation-language@2"]
)
def test_fake_run_writes_a_complete_report(suite: str, tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    code = main(
        [
            "run",
            "--suite",
            suite,
            "--config",
            str(CONFIGS / "fake.yaml"),
            "--repeat",
            "2",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    report = read_report(out)
    assert report.suite == suite and report.split == "dev" and not report.gate_mode
    assert report.config.provider == "fake" and len(report.config.sha256) == 64
    assert report.cost.total_micro_eur == 0 and report.cost.unknown_calls == 0
    assert report.versions.pricing == "pricing-v2" and report.versions.gates == "gates-v4"
    assert report.models_recorded == report.models_returned  # The fake reports its model.
    assert report.dataset.synthetic and report.dataset.manifest_sha256
    assert report.metrics["structured_after_retry"] == 1.0
    assert report.metrics["stability.changed_rate"] == 0.0  # The fake is deterministic.


def test_diagnose_lists_failed_items_of_a_dev_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "report.json"
    argv = ["run", "--suite", "evaluation-language@2", "--config", str(CONFIGS / "fake.yaml")]
    assert main([*argv, "--repeat", "1", "--out", str(out)]) == 0
    capsys.readouterr()
    assert main(["diagnose", str(out)]) == 0
    text = capsys.readouterr().out
    # The fake evaluator misses annotated errors: the failed items and what each pass reported
    # and missed are readable in a log.
    assert text.startswith("Diagnostic de evaluation-language@2 (dev) :")
    assert "en échec" in text and "passage 1 : signalé [" in text and "manqué [" in text
    # What changed between repetitions can be a code switch or a section, not only an error.
    assert "alternances [" in text and "attendues [" in text


def test_diagnose_shows_nothing_of_a_gate_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "gate.json"
    argv = ["run", "--suite", "evaluation-language@2", "--config", str(CONFIGS / "fake.yaml")]
    main([*argv, "--split", "holdout", "--gate", "--out", str(out)])
    capsys.readouterr()
    assert main(["diagnose", str(out)]) == 0
    assert "réservé au split dev" in capsys.readouterr().out


def test_gate_run_on_the_holdout_exits_non_zero_when_it_fails(tmp_path: Path) -> None:
    out = tmp_path / "gate.json"
    code = main(
        [
            "run",
            "--suite",
            "evaluation-language@1",
            "--config",
            str(CONFIGS / "fake.yaml"),
            "--split",
            "holdout",
            "--gate",
            "--out",
            str(out),
        ]
    )
    report = read_report(out)
    assert report.repetitions == 3 and report.gate is not None
    assert code == (1 if report.gate.status == "failed" else 0)
    assert all(r.detail is None for item in report.items for r in item.runs)


def test_holdout_without_gate_and_short_gate_runs_are_refused(tmp_path: Path) -> None:
    base = [
        "run",
        "--suite",
        "patient-leakage@1",
        "--config",
        str(CONFIGS / "fake.yaml"),
        "--out",
        str(tmp_path / "r.json"),
    ]
    assert main([*base, "--split", "holdout"]) == 2
    assert main([*base, "--gate", "--repeat", "2"]) == 2
    assert not (tmp_path / "r.json").exists()


def test_a_real_model_configuration_needs_live(tmp_path: Path) -> None:
    live_configs = [p.name for p in CONFIGS.glob("*.yaml") if load_config(p)[0].is_live]
    assert len(live_configs) >= 4
    for name in live_configs:
        code = main(
            [
                "run",
                "--suite",
                "patient-leakage@1",
                "--config",
                str(CONFIGS / name),
                "--out",
                str(tmp_path / "r.json"),
            ]
        )
        assert code == 2
    live = EvalConfig.model_validate(
        {
            "id": "x",
            "description": "x",
            "provider": "live",
            "routes": {
                "patient": {"provider": "openai", "model": "gpt-6-luna"},
                "evaluation": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
            },
            "prompts": {"patient": "patient-v3"},
        }
    )
    with pytest.raises(ConfigError, match="--live"):
        build_provider(live, live=False, pricing=PRICING)


async def test_a_failing_provider_shows_its_own_error_in_the_diagnostic() -> None:
    from types import SimpleNamespace
    from typing import Any

    from scripted import ScriptedProvider, request_for

    from ari.application.contracts import LLMRequest
    from ari.domain.errors import ProviderError
    from ari.evals.report import diagnosis_text
    from ari.evals.runner import run
    from ari.evals.scorers.evaluation_language import EvaluationLanguageSuite

    class Refusing(ScriptedProvider):
        async def generate_structured(self, request: LLMRequest, response_model: Any) -> Any:
            execution = SimpleNamespace(
                error_code="RateLimitError", error_message="Error code: 429 - quota exceeded"
            )
            raise ProviderError("OpenAI structured generation failed", execution=execution)

    report = await run(request_for(EvaluationLanguageSuite(), Refusing(lambda r, p: {})))
    text = diagnosis_text(report)
    assert "causes : " in text and "RateLimitError : Error code: 429 - quota exceeded" in text
    assert "erreur du fournisseur :" in text


def _live_config(**routes: dict[str, object]) -> dict[str, object]:
    base: dict[str, object] = {
        "patient": {"provider": "openai", "model": "gpt-6-luna"},
        "evaluation": {"provider": "openai", "model": "gpt-6-luna"},
    }
    return {
        "id": "x",
        "description": "x",
        "provider": "live",
        "routes": {**base, **routes},
        "prompts": {"patient": "patient-v3", "evaluation": "evaluation-v10"},
    }


def test_sampling_settings_label_the_evaluation_route_and_apply_to_it_only() -> None:
    sampled = {
        "provider": "openai",
        "model": "gpt-6-luna",
        "reasoning_effort": "high",
        "seed": 7,
    }
    config = EvalConfig.model_validate(_live_config(evaluation=sampled))
    assert config.route_labels()["evaluation"] == "openai:gpt-6-luna[reasoning_effort=high,seed=7]"
    assert config.route_labels()["patient"] == "openai:gpt-6-luna"
    with pytest.raises(ValueError, match="OpenAI evaluation route"):
        EvalConfig.model_validate(_live_config(patient=sampled))
    with pytest.raises(ValueError, match="OpenAI evaluation route"):
        EvalConfig.model_validate(
            _live_config(
                evaluation={**sampled, "provider": "anthropic", "model": "claude-sonnet-5-5"}
            )
        )


def test_the_medical_suite_waits_for_the_physician(capsys: pytest.CaptureFixture[str]) -> None:
    assert not (DATASETS / "evaluation-medical" / "1" / "manifest.json").exists()
    code = main(["run", "--suite", "evaluation-medical@1", "--config", str(CONFIGS / "fake.yaml")])
    assert code == 2 and "evaluation-medical@1" in capsys.readouterr().err


def test_compare_command_prints_the_recommendation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = []
    for config_id in ("fake", "fake-copy"):
        config_path = tmp_path / f"{config_id}.yaml"
        config_path.write_text(
            (CONFIGS / "fake.yaml").read_text("utf-8").replace("id: fake", f"id: {config_id}"),
            encoding="utf-8",
        )
        out = tmp_path / f"{config_id}.json"
        assert (
            main(
                [
                    "run",
                    "--suite",
                    "patient-leakage@1",
                    "--config",
                    str(config_path),
                    "--repeat",
                    "3",
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        paths.append(str(out))
    capsys.readouterr()
    assert main(["compare", *paths, "--out", str(tmp_path / "cmp.json")]) == 0
    printed = capsys.readouterr().out
    assert "Recommandation" in printed and (tmp_path / "cmp.json").is_file()


def test_every_committed_configuration_loads_and_is_priced() -> None:
    """A live route without a price would make every cost of the run unknown."""
    for path in sorted(CONFIGS.glob("*.yaml")):
        config, digest = load_config(path)
        assert len(digest) == 64
        load_prompts(config, ("patient", "evaluation"), PROMPTS)
        for task, route in config.routes.items():
            assert (route.provider, route.model) in PRICING.models, f"{path.name}: {task}"
        if config.patient_engine == "exam":
            assert config.prompts["patient"] == "patient-exam-v1"


def test_fake_run_on_the_exam_chain(tmp_path: Path) -> None:
    out = tmp_path / "exam.json"
    code = main(
        ["run", "--suite", "patient-leakage@1", "--config", str(CONFIGS / "fake-exam.yaml"),
         "--out", str(out)]
    )  # fmt: skip
    assert code == 0
    report = read_report(out)
    assert report.config.patient_engine == "exam"
    assert report.versions.prompts["patient"].version == "patient-exam-v1"
    assert report.metrics["structured_after_retry"] == 1.0
