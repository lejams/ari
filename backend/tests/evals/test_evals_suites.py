"""Suites run through the real services with scripted providers: what each scorer counts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripted import DATASETS, GATES, ScriptedProvider, item_id_of, repetition_of, request_for

from ari.application.contracts import LLMRequest
from ari.evals.configs import ConfigError
from ari.evals.gates import load_gates
from ari.evals.manifest import load_dataset
from ari.evals.runner import run
from ari.evals.scorers.evaluation_language import EvaluationLanguageSuite, LanguageItem
from ari.evals.scorers.evaluation_medical import EvaluationMedicalSuite
from ari.evals.scorers.patient_leakage import LeakageItem, PatientLeakageSuite

LEAKAGE = load_dataset(DATASETS, "patient-leakage", "1")
LANGUAGE = load_dataset(DATASETS, "evaluation-language", "1")
GATE_RULES, _ = load_gates(GATES)


def _leakage_items() -> dict[str, LeakageItem]:
    return {
        row["id"]: LeakageItem.model_validate_json(json.dumps(row))  # type: ignore[misc]
        for row in LEAKAGE.read_split("dev", gate=False)
    }


def _language_items() -> dict[str, LanguageItem]:
    return {
        row["id"]: LanguageItem.model_validate_json(json.dumps(row))  # type: ignore[misc]
        for row in LANGUAGE.read_split("dev", gate=False)
    }


def oracle_patient(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
    """Answers exactly what the annotation expects."""
    item = _leakage_items()[item_id_of(request)]
    kind = item.expected_kinds[0]
    refs = [f"fact:{f}" for f in item.expected_fact_ids] if kind == "sources" else []
    if kind == "sources" and not refs:
        refs = [f"fact:{item.allowed_fact_ids[0]}"]
    return {"response_kind": kind, "source_refs": refs}


def _evaluation_output(
    payload: dict[str, Any], errors: list[dict[str, Any]], switches: list[dict[str, Any]]
) -> dict[str, Any]:
    turns = [t["turn"] for t in payload["transcript"]]
    return {
        "summary": "Synthetische Rückmeldung.",
        "criteria": [
            {
                "criterion_id": c["id"],
                "score": 3,
                "evidence_turn_sequences": turns[:1],
                "feedback": "ok",
            }
            for c in payload["rubric"]
        ],
        "language_errors": errors,
        "code_switches": switches,
        "empathy": [],
    }


def oracle_evaluator(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
    truth = _language_items()[item_id_of(request)].truth
    return _evaluation_output(
        payload,
        [
            {"text": "Fehler", "category": e.category, "evidence_turn_sequences": [e.turn]}
            for e in truth.language_errors
        ],
        [
            {"turn": c.turn, "fragment": "frz.", "intended_term": c.intended_term}
            for c in truth.code_switches
        ],
    )


async def test_patient_oracle_passes_every_leakage_check() -> None:
    report = await run(request_for(PatientLeakageSuite(), ScriptedProvider(oracle_patient)))
    assert report.metrics["leak_rate"] == 0.0
    assert report.metrics["refusal.recall"] == 1.0
    assert report.metrics["over_refusal_rate"] == 0.0
    assert report.metrics["structured_after_retry"] == 1.0
    assert all(item.verdict for item in report.items)
    assert report.cost.unknown_calls == 0 and report.cost.mean_micro_eur_per_case == 176


async def test_patient_leak_refusal_and_over_refusal_are_counted() -> None:
    items = _leakage_items()

    def leaky(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        item = items[item_id_of(request)]
        if item.id == "brustschmerz-12":  # Pets: absent; reveals smoking instead.
            return {"response_kind": "sources", "source_refs": ["fact:smoking"]}
        if item.id == "brustschmerz-17":  # French question answered.
            return {"response_kind": "sources", "source_refs": ["fact:onset"]}
        if item.id == "brustschmerz-03":  # Legitimate question refused.
            return {"response_kind": "out_of_scope", "source_refs": []}
        return oracle_patient(request, payload)

    report = await run(request_for(PatientLeakageSuite(), ScriptedProvider(leaky)))
    by_id = {item.item_id: item for item in report.items}
    assert by_id["brustschmerz-12"].outcomes == ["fact_leak"]
    assert by_id["brustschmerz-17"].outcomes == ["fact_leak", "refusal_missed"]
    assert by_id["brustschmerz-03"].outcomes == ["answer_incomplete", "over_refusal"]
    assert report.metrics["leak_rate"] == pytest.approx(2 / 60)
    detail = by_id["brustschmerz-12"].runs[0].detail
    assert detail is not None and detail["leaked_fact_ids"] == ["smoking"]


async def test_an_invented_fact_is_critical_even_when_the_retry_succeeds() -> None:
    def inventive(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        if item_id_of(request) == "bauchschmerz-06" and inventive.calls == 0:  # type: ignore[attr-defined]
            inventive.calls += 1  # type: ignore[attr-defined]
            return {"response_kind": "sources", "source_refs": ["fact:appendectomy"]}
        return oracle_patient(request, payload)

    inventive.calls = 0  # type: ignore[attr-defined]
    provider = ScriptedProvider(inventive)
    report = await run(
        request_for(
            PatientLeakageSuite(), provider, gates=GATE_RULES, gate_mode=True, repetitions=3
        )
    )
    item = next(i for i in report.items if i.item_id == "bauchschmerz-06")
    first = item.runs[0]
    assert first.attempts == 2 and first.usable and first.outcomes == ["invented_fact"]
    assert len(first.calls) == 2  # Both attempts are recorded and costed.
    assert report.critical_errors == ["bauchschmerz-06:invented_fact"]
    assert report.metrics["structured_first_try"] < 1.0
    assert report.metrics["structured_after_retry"] == 1.0
    assert report.gate is not None and report.gate.status == "failed"
    assert report.gate.new_critical_errors == ["bauchschmerz-06:invented_fact"]
    assert provider.calls == 60 * 3 + 1


async def test_language_oracle_scores_perfectly_and_measures_reliability() -> None:
    report = await run(
        request_for(EvaluationLanguageSuite(), ScriptedProvider(oracle_evaluator), repetitions=3)
    )
    for category in ("gender", "case", "verb_form", "word_order"):
        assert report.metrics[f"language.precision.{category}"] == 1.0
        assert report.metrics[f"language.recall.{category}"] == 1.0
    assert report.metrics["stt_false_error_rate"] == 0.0
    assert report.metrics["evidence_hallucination_rate"] == 0.0
    assert report.metrics["code_switch.recall.all"] == 1.0
    assert report.metrics["forgotten_section.recall.all"] == 1.0
    assert report.metrics["stability.changed_rate"] == 0.0
    [reliability] = report.reliability
    assert (reliability.source, reliability.precision) == ("llm", 1.0)
    assert reliability.producer == "evaluation-v6@gpt-6-luna"
    assert report.versions.evaluation_schema_version == "session-evaluation-v4"
    assert set(report.versions.output_schemas) == {"EvaluationOutputSchema"}


async def test_language_false_errors_stt_blame_and_hallucinated_evidence() -> None:
    items = _language_items()

    def noisy(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        truth = items[item_id_of(request)].truth
        errors = [
            # Right turn, wrong category: a false error and a missed one, not hallucinated.
            {
                "text": "x",
                "category": "register" if e.category == "gender" else e.category,
                "evidence_turn_sequences": [e.turn],
            }
            for e in truth.language_errors
        ] + [
            # Every speech-to-text artefact blamed on the learner.
            {"text": "x", "category": "word_choice", "evidence_turn_sequences": [turn]}
            for turn in truth.stt_artifact_turns
        ]
        return _evaluation_output(payload, errors[:6], [])

    report = await run(request_for(EvaluationLanguageSuite(), ScriptedProvider(noisy)))
    metrics = report.metrics
    assert metrics["language.recall.gender"] == 0.0
    assert metrics["language.precision.register"] == 0.0
    assert metrics["language.recall.case"] == 1.0
    assert metrics["stt_false_error_rate"] == pytest.approx(1.0, abs=0.05)
    assert metrics["category_confusion_rate"] is not None and metrics["category_confusion_rate"] > 0
    stt = sum(len(i.truth.stt_artifact_turns) for i in items.values())
    injected = sum(len(i.truth.language_errors) for i in items.values())
    assert metrics["evidence_hallucination_rate"] == pytest.approx(stt / (stt + injected))
    assert metrics["code_switch.recall.all"] == 0.0


async def test_a_report_on_the_holdout_keeps_its_content_out() -> None:
    report = await run(
        request_for(
            EvaluationLanguageSuite(),
            ScriptedProvider(lambda r, p: _holdout_oracle(r, p)),
            split="holdout",
            gate_mode=True,
            gates=GATE_RULES,
            repetitions=3,
        )
    )
    assert report.split == "holdout"
    assert all(run_entry.detail is None for item in report.items for run_entry in item.runs)
    assert report.gate is not None and report.gate.status == "passed"


def _holdout_oracle(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
    rows = {r["id"]: r for r in LANGUAGE.read_split("holdout", gate=True)}
    truth = LanguageItem.model_validate_json(json.dumps(rows[item_id_of(request)])).truth
    return _evaluation_output(
        payload,
        [
            {"text": "f", "category": e.category, "evidence_turn_sequences": [e.turn]}
            for e in truth.language_errors
        ],
        [{"turn": c.turn, "fragment": "f"} for c in truth.code_switches],
    )


async def test_unstable_answers_raise_the_changed_verdict_rate() -> None:
    def flaky(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        output = oracle_evaluator(request, payload)
        if repetition_of(request) == 2:
            output["code_switches"] = []
        return output

    report = await run(
        request_for(EvaluationLanguageSuite(), ScriptedProvider(flaky), repetitions=3)
    )
    with_switches = sum(1 for i in _language_items().values() if i.truth.code_switches)
    assert report.metrics["stability.changed_rate"] == pytest.approx(with_switches / 90)


# --- evaluation-medical: no physician data yet; a temporary synthetic dataset in tmp_path ---


def _medical_dataset(root: Path) -> None:
    directory = root / "evaluation-medical" / "1"
    directory.mkdir(parents=True)
    spec = json.loads((DATASETS / "patient-leakage" / "1" / "cases.json").read_text("utf-8"))
    case = spec["cases"][0]
    case["assessment_items"] = [
        {
            "id": "ask-radiation",
            "label": "Ausstrahlung",
            "satisfied_by_fact_ids": ["radiation"],
            "required": True,
            "criticality": "critical",
        },
        {
            "id": "ask-allergy",
            "label": "Allergien",
            "satisfied_by_fact_ids": ["allergy"],
            "required": True,
        },
    ]
    turns = [
        {
            "sequence": 1,
            "doctor": "Was führt Sie zu uns?",
            "patient": "Brustschmerzen.",
            "revealed_fact_ids": ["reason"],
        },
        {
            "sequence": 2,
            "doctor": "Haben Sie Allergien?",
            "patient": "Penicillin.",
            "revealed_fact_ids": ["allergy"],
        },
    ]
    rows = {
        "dev": [
            {
                "id": "test-01",
                "family": "f-dev",
                "case_id": case["id"],
                "turns": turns,
                "annotation": {
                    "annotator": "test:synthetic",
                    "required_omissions": [{"item_id": "ask-radiation", "critical": True}],
                    "medical_errors": [{"turn": 2, "description": "synthetic", "critical": True}],
                },
            }
        ],
        "holdout": [
            {
                "id": "test-02",
                "family": "f-holdout",
                "case_id": case["id"],
                "turns": turns,
                "annotation": {"annotator": "test:synthetic", "required_omissions": []},
            }
        ],
    }
    files = {
        "cases.json": json.dumps({"cases": [case]}),
        "dev.jsonl": json.dumps(rows["dev"][0]) + "\n",
        "holdout.jsonl": json.dumps(rows["holdout"][0]) + "\n",
    }
    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")
    manifest = {
        "suite": "evaluation-medical",
        "version": "1",
        "description": "test",
        "synthetic": True,
        "source": "test",
        "created_on": "2026-10-20",
        "consent": "not_applicable:synthetic",
        "annotators": [{"id": "test", "role": "test", "annotated_on": "2026-10-20"}],
        "splits": {"dev": ["f-dev"], "holdout": ["f-holdout"]},
        "files": {n: hashlib.sha256(c.encode()).hexdigest() for n, c in files.items()},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


async def test_medical_suite_counts_statement_errors_as_not_detected(tmp_path: Path) -> None:
    _medical_dataset(tmp_path)
    dataset = load_dataset(tmp_path, "evaluation-medical", "1")
    provider = ScriptedProvider(lambda r, p: _evaluation_output(p, [], []))
    report = await run(
        request_for(EvaluationMedicalSuite(), provider, dataset=dataset, gates=GATE_RULES)
    )
    # The deterministic assessment finds the critical omission; the statement error is
    # invisible to session-evaluation-v4, so critical recall is 1 / 2.
    assert report.metrics["omission.recall.all"] == 1.0
    assert report.metrics["omission.precision.all"] == 1.0
    assert report.metrics["critical_medical_error.recall"] == 0.5
    assert report.metrics["statement_error.recall"] == 0.0
    assert report.critical_errors == ["test-01:missed_critical_medical_error"]
    [reliability] = report.reliability
    assert (reliability.source, reliability.producer) == ("deterministic", "assessment-weighted-v1")


async def test_exam_chain_non_clinical_turns_reveal_nothing_and_count_as_refusals() -> None:
    items = _leakage_items()

    def exam(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        assert "patient_persona" in payload  # The exam chain request, not the training one.
        item = items[item_id_of(request)]
        if item.id == "atemnot-14":  # Football: chit-chat in allowed words, no fact.
            return {"response_kind": "small_talk", "source_refs": [], "free_text": "Ja, danke."}
        if item.id == "atemnot-15":  # Clinical claim in free wording: the screen rejects it.
            return {
                "response_kind": "small_talk",
                "source_refs": [],
                "free_text": "Ich habe Fieber.",
            }
        if item.id == "atemnot-04":  # A legitimate question met with a clarification request.
            return {
                "response_kind": "clarification_request",
                "source_refs": [],
                "free_text": "Wie bitte?",
            }
        return oracle_patient(request, payload)

    report = await run(request_for(PatientLeakageSuite(), ScriptedProvider(exam), engine="exam"))
    by_id = {item.item_id: item for item in report.items}
    assert by_id["atemnot-14"].outcomes == [] and by_id["atemnot-15"].outcomes == []
    assert by_id["atemnot-04"].outcomes == ["answer_incomplete", "over_refusal"]
    assert report.metrics["leak_rate"] == 0.0
    assert report.metrics["refusal.any_recall"] == 1.0
    assert report.metrics["refusal.recall"] < 1.0  # Small talk is not the expected kind.
    assert report.metrics["free_text_rejection_rate"] == pytest.approx(1 / 3)
    assert report.models_returned == {"patient_simulation": ["gpt-6-luna-2026-10-01"]}


def test_an_unusable_medical_evaluation_still_counts_its_critical_error(tmp_path: Path) -> None:
    _medical_dataset(tmp_path)
    dataset = load_dataset(tmp_path, "evaluation-medical", "1")
    suite = EvaluationMedicalSuite()
    [row] = dataset.read_split("dev", gate=False)
    item = suite.parse_item(row)
    score = suite.score(item, dataset.cases[item.case_id].to_medical_case(), None, [])
    assert score.counts["omission.fn.all"] == 1
    assert score.outcomes == ("missed_critical_medical_error",)


def test_an_unusable_evaluation_counts_its_annotated_errors_as_missed() -> None:
    suite = EvaluationLanguageSuite()
    item = next(i for i in _language_items().values() if i.truth.language_errors)
    case = LANGUAGE.cases[item.case_id].to_medical_case()
    counts = suite.score(item, case, None, []).counts
    for error in item.truth.language_errors:
        assert counts[f"lang.fn.{error.category}"] >= 1
    assert counts.get("cs.fn.all", 0) == len({c.turn for c in item.truth.code_switches})
    assert counts.get("section.fn.all", 0) == len(set(item.truth.missing_sections))
    assert not any(key.startswith("lang.tp.") for key in counts)


async def test_a_gate_run_refuses_a_baseline_from_other_inputs() -> None:
    request = request_for(
        PatientLeakageSuite(),
        ScriptedProvider(oracle_patient),
        repetitions=GATE_RULES.repetitions,
        gates=GATE_RULES,
    )
    baseline = await run(request)
    gated = replace(request, gate_mode=True)
    assert (await run(replace(gated, baseline=baseline))).gate is not None
    # Another dataset version: refused before anything runs.
    other_version = baseline.model_copy(
        update={"dataset": baseline.dataset.model_copy(update={"manifest_sha256": "0" * 64})}
    )
    with pytest.raises(ConfigError, match="même jeu"):
        await run(replace(gated, baseline=other_version))
    # Same version but other items under the same ids would exempt new critical errors.
    other_items = baseline.model_copy(update={"items": baseline.items[1:]})
    with pytest.raises(ConfigError, match="items diffèrent"):
        await run(replace(gated, baseline=other_items))


async def test_a_gate_run_without_thresholds_for_the_dataset_is_refused() -> None:
    gates = GATE_RULES.model_copy(update={"suites": {}})
    request = request_for(
        EvaluationLanguageSuite(),
        ScriptedProvider(oracle_evaluator),
        gate_mode=True,
        repetitions=GATE_RULES.repetitions,
        gates=gates,
    )
    with pytest.raises(ConfigError, match="pas de seuils"):
        await run(request)
