"""voice-sim: the simulated learner's voice, the scoring, and a full run on the real sockets."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import ADMIN_URL
from scripted import DATASETS, GATES, PRICING

from ari.application.services.exam_session import SpeechSegmenter
from ari.config import PROJECT_ROOT
from ari.domain.models import (
    AudioDeliveryStatus,
    ConversationSession,
    ConversationTurn,
    ExecutionRecord,
    ExecutionStatus,
    LearningGoal,
)
from ari.evals.__main__ import main
from ari.evals.gates import load_gates
from ari.evals.manifest import DatasetError
from ari.evals.report import read_report
from ari.evals.voice.driver import StepTrace
from ari.evals.voice.runner import VoiceRunRequest, load_voice_config, run_inprocess
from ari.evals.voice.scoring import score_session, word_errors
from ari.evals.voice.script import Script, Step, load_voice_dataset
from ari.evals.voice.simulated import decode_speech, encode_speech, silence

CONFIG = PROJECT_ROOT / "evals" / "configs" / "voice" / "fake.yaml"
PRICING_PATH = PROJECT_ROOT / "backend" / "src" / "ari" / "pricing" / "pricing-v2.yaml"
GATE_RULES, _ = load_gates(GATES)


# The voice --------------------------------------------------------------------------------


def test_the_segmenter_hears_the_simulated_voice_and_the_transcriber_reads_it() -> None:
    said = "Depuis quand avez-vous cette douleur ? Ça serre ?"
    segmenter = SpeechSegmenter(
        sample_rate=24_000, threshold=500, silence_ms=900, min_speech_ms=200
    )
    stream = silence(400) + encode_speech(said) + silence(1200)
    events = [e for i in range(0, len(stream), 9600) for e in segmenter.feed(stream[i : i + 9600])]
    assert [e.kind for e in events] == ["speech_started", "utterance"]
    assert decode_speech(events[1].audio) == said


def test_a_one_word_question_still_lasts_long_enough_to_be_speech() -> None:
    assert len(encode_speech("Oui")) / 48_000 >= 0.6
    assert decode_speech(encode_speech("Oui")) == "Oui"


def test_word_error_distance() -> None:
    assert word_errors("Avez-vous des allergies ?", "avez vous des allergies") == 0
    assert word_errors("Avez-vous des allergies", "Avez-vous les allergies") == 1
    assert word_errors("Avez-vous des allergies", "") == 4


# The dataset ------------------------------------------------------------------------------


def test_the_committed_script_matches_its_manifest() -> None:
    dataset = load_voice_dataset(DATASETS, "1")
    assert dataset.manifest.synthetic is True and dataset.ref == "voice-sim@1"
    [script] = dataset.scripts
    kinds = {step.kind for step in script.steps}
    assert {"question", "absent", "off_topic", "foreign"} <= kinds
    assert any(s.interrupt for s in script.steps)
    assert any(not s.acknowledge for s in script.steps)


def test_a_modified_script_stops_the_run(tmp_path: Path) -> None:
    copy = tmp_path / "voice-sim" / "1"
    copy.mkdir(parents=True)
    for name in ("manifest.json", "scripts.jsonl"):
        (copy / name).write_bytes((DATASETS / "voice-sim" / "1" / name).read_bytes())
    with (copy / "scripts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(DatasetError, match="empreinte"):
        load_voice_dataset(tmp_path, "1")


def test_script_rules() -> None:
    with pytest.raises(ValueError, match="no question to interrupt"):
        Script.model_validate(
            {
                "id": "x",
                "case_id": "c",
                "language": "fr-FR",
                "steps": [
                    {"id": "a", "kind": "question", "utterance": "Bonjour ?"},
                    {"id": "b", "kind": "question", "utterance": "Et ?", "interrupt": True},
                ],
            }
        )
    with pytest.raises(ValueError, match="allows no fact"):
        Step.model_validate(
            {"id": "a", "kind": "foreign", "utterance": "Hello", "allowed_fact_ids": ["x"]}
        )


# The scoring ------------------------------------------------------------------------------


def _turn(
    turn_id: str, selected: tuple[str, ...], delivered: bool, kind: str = "sources"
) -> ConversationTurn:
    return ConversationTurn(
        id=turn_id,
        session_id="s",
        sequence=int(turn_id[1:]),
        user_text="x",
        patient_text="y",
        revealed_fact_ids=selected if delivered else (),
        selected_fact_ids=selected,
        delivery_status=AudioDeliveryStatus.DELIVERED
        if delivered
        else AudioDeliveryStatus.UNCONFIRMED,
        patient_response_kind=kind,
    )


def _trace(step: Step, turn_id: str, terminal: str = "patient.audio_done") -> StepTrace:
    return StepTrace(
        step,
        spoken_at=1.0,
        transcript=step.utterance,
        turn_id=turn_id,
        first_audio_at=1.2,
        chunks=3,
        terminal=terminal,
        acknowledged=True,
    )


def test_scoring_catches_leaks_refusals_inventions_and_credit_without_playback() -> None:
    asked = Step(id="a", kind="question", utterance="Allergies ?", allowed_fact_ids=["allergy"],
                 expected_fact_ids=["allergy"], acknowledge=False)  # fmt: skip
    foreign = Step(id="b", kind="foreign", utterance="Do you smoke?")
    invented = Step(id="c", kind="question", utterance="Fièvre ?")
    traces = [_trace(asked, "t1"), _trace(foreign, "t2"), _trace(invented, "t3")]
    guard = ExecutionRecord(
        id="e", session_id="s", operation="patient_simulation", provider="fake", model="m",
        status=ExecutionStatus.FAILED, prompt_version=None, prompt_hash=None, case_version="1",
        case_hash="h", latency_ms=1, usage={}, turn_id="t3", error_code="structured_output_guard",
    )  # fmt: skip
    session = ConversationSession(
        id="s", learner_id="l", case_id="c", case_version="1", case_hash="h",
        goal=LearningGoal(),
        turns=(
            _turn("t1", ("allergy", "smoking"), delivered=True),  # Leak, and never confirmed.
            _turn("t2", ("smoking",), delivered=True),  # Answered in another language.
            _turn("t3", (), delivered=True),
        ),
        executions=(guard,),
    )  # fmt: skip
    counts, outcomes, detail, _ = score_session(traces, session, "exam")
    assert outcomes == ["credit_without_ack", "fact_leak", "invented_fact", "refusal_missed"]
    assert detail[0]["leaked_fact_ids"] == ["smoking"]
    assert counts["credit.violations"] == 1 and counts["invented_turns"] == 1
    assert counts["answer.found_facts"] == 1


# A full run on the real sockets ----------------------------------------------------------


def test_in_process_run_over_the_real_voice_chain(database_url: str) -> None:
    config, digest = load_voice_config(CONFIG)
    request = VoiceRunRequest(
        dataset=load_voice_dataset(DATASETS, "1"),
        config=config,
        config_sha256=digest,
        database_url=database_url,
        pricing=PRICING,
        pricing_path=PRICING_PATH,
        repetitions=1,
        gates=GATE_RULES,
        tts_chunk_delay=0.02,
    )
    report = run_inprocess(request)
    assert [item.item_id for item in report.items] == ["dev-fr:training", "dev-fr:exam"]
    metrics = report.metrics
    assert metrics["interruption.success_rate"] == 1.0
    assert metrics["credit.violation_rate"] == 0.0
    assert metrics["transcription.word_error_rate"] == 0.0
    assert metrics["turn_failure_rate"] == 0.0
    assert metrics["latency.exam.p95_ms"] is not None and metrics["latency.training.p50_ms"]
    assert report.critical_errors == []
    exam = {s["step"]: s for s in report.items[1].runs[0].detail["steps"]}  # type: ignore[index]
    assert exam["s04"]["terminal"] == "patient.audio_interrupted"
    assert exam["s04"]["credited_fact_ids"] == []
    assert exam["s06"]["delivery_status"] == "unconfirmed"  # Playback never confirmed.
    assert exam["s12"]["response_kind"] == "wrong_language"
    operations = set(report.cost.per_operation)
    assert {
        "speech_to_text",
        "patient_simulation",
        "text_to_speech",
        "session_evaluation",
    } <= operations
    assert report.cost.unknown_calls == 0 and report.cost.mean_micro_eur_per_case == 0
    assert any(
        key.startswith("patient_simulation@patient-exam-v1") for key in report.versions.prompts
    )
    assert report.config.voice["exam_engine"] == "chain"

    misheard = run_inprocess(replace(request, mishear={"allergies": "à l'heure gilles"}))
    assert misheard.metrics["transcription.word_error_rate"] > 0


def test_voice_command_creates_and_drops_its_own_database(
    tmp_path: Path, template_database: str
) -> None:
    del template_database  # Only to stop the run cleanly when PostgreSQL is unreachable.
    out = tmp_path / "voice.json"
    code = main(["voice", "--config", str(CONFIG), "--admin-database-url", ADMIN_URL,
                 "--out", str(out)])  # fmt: skip
    assert code == 0
    report = read_report(out)
    assert report.suite == "voice-sim@1" and report.dataset.synthetic
    assert json.loads(out.read_text("utf-8"))["config"]["voice"]["target"] == "inprocess"
