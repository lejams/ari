"""Exam mode: open microphone, speech-to-speech stack, LLM audit of revealed facts."""

from __future__ import annotations

import base64
from dataclasses import replace
from types import SimpleNamespace

import pytest
from accounts_fixtures import sign_in
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.application.contracts import ExecutionContext
from ari.application.services.realtime_patient import realtime_instructions
from ari.container import PIPELINE_STACK_ID, REALTIME_STACK_ID, Container
from ari.domain.errors import InvalidStateError
from ari.domain.models import ExecutionStatus, LearningMode, TurnResponseState
from ari.infrastructure.providers.fake import FakeRealtimeEngine
from ari.infrastructure.providers.openai.realtime import (
    OpenAIRealtimeEngine,
    RealtimeEventTranslator,
)


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


def create_session(client: TestClient, mode: str) -> dict[str, object]:
    case = client.get("/api/cases").json()[0]
    sign_in(client)
    learner = client.post("/api/learners", json={}).json()
    return client.post(
        "/api/sessions",
        json={
            "learner_id": learner["id"],
            "case_id": case["id"],
            "case_version": case["version"],
            "learning_mode": mode,
        },
    ).json()


def receive(socket: object, wanted: str) -> dict[str, object]:
    while True:
        event = socket.receive_json()  # type: ignore[attr-defined]
        if event["type"] == wanted:
            return event["data"]


def test_exam_sessions_pin_the_realtime_stack_and_training_keeps_the_pipeline(
    container: Container,
) -> None:
    with TestClient(create_app(container)) as client:
        exam = create_session(client, "exam")
        training = create_session(client, "training")
    assert exam["voice_stack_id"] == REALTIME_STACK_ID
    assert exam["voice_stack_config"]["models"]["sts"] == container.settings.realtime_model
    assert training["voice_stack_id"] == PIPELINE_STACK_ID
    stored = container.repository.get_session(str(exam["id"]))
    assert stored.learning_mode is LearningMode.EXAM
    assert container.resolve_voice_stack(stored) is container.realtime_stack
    with pytest.raises(InvalidStateError):
        container.resolve_voice_stack(replace(stored, voice_stack_id="unknown"))


def test_open_microphone_turn_is_persisted_audited_and_credited_on_playback(
    container: Container,
) -> None:
    engine = container.realtime
    assert isinstance(engine, FakeRealtimeEngine)
    with TestClient(create_app(container)) as client:
        session_id = str(create_session(client, "exam")["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            started = receive(socket, "call.started")
            assert started["interaction"] == "open_microphone"
            assert started["voice_stack_id"] == REALTIME_STACK_ID
            # Microphone frames flow straight to the model; the fake reads UTF-8 as speech.
            socket.send_bytes("Erzählen Sie von fact-1.".encode())
            receive(socket, "user.speech_started")
            assert receive(socket, "user.transcript_final")["text"] == "Erzählen Sie von fact-1."
            streaming = receive(socket, "patient.audio_streaming")
            chunk = receive(socket, "patient.audio_chunk")
            assert chunk["mime_type"] == "audio/pcm;rate=24000"
            assert len(base64.b64decode(chunk["audio"])) % 2 == 0
            # Playback may start before the turn exists: the ack is held, not rejected.
            early = {
                "turn_id": streaming["turn_id"],
                "response_id": streaming["response_id"],
                "audio_stream_id": streaming["audio_stream_id"],
                "last_index": 0,
            }
            socket.send_json({"type": "audio.playback_started", **early})
            receive(socket, "turn.persisted")
            assert receive(socket, "patient.response_text")["text"] == "Erzählen Sie von fact-1."
            sent = receive(socket, "patient.audio_sent")
            assert sent["turn_id"] == streaming["turn_id"]
            receive(socket, "turn.completed")
            receive(socket, "turn.audio_started")
            turn = container.repository.get_session(session_id).turns[0]
            assert turn.user_text == "Erzählen Sie von fact-1."
            assert turn.provider_input_item_id == "fake-item-1"
            assert turn.provider_response_id == streaming["response_id"]
            assert turn.selected_fact_ids == ("fact-1",)
            assert turn.revealed_fact_ids == ()
            assert turn.response_state is TurnResponseState.AUDIO_STARTED
            socket.send_json(
                {"type": "audio.playback_completed", **early, "last_index": sent["last_index"]}
            )
            receive(socket, "turn.delivered")
            assert container.repository.get_session(session_id).turns[0].revealed_fact_ids == (
                "fact-1",
            )
            socket.send_json({"type": "turn.retry_tts", "turn_id": streaming["turn_id"]})
            receive(socket, "turn.retry_rejected")
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
        operations = {
            (item.operation, item.status)
            for item in container.repository.get_session(session_id).executions
        }
        assert ("speech_to_speech", ExecutionStatus.SUCCEEDED) in operations
        assert ("fact_attribution", ExecutionStatus.SUCCEEDED) in operations
        assert engine.instructions and "fact-1" in engine.instructions[0]
        ended = client.post(f"/api/sessions/{session_id}/end", json={}).json()
        assert ended["status"] == "completed"
        assert ended["turns"][0]["provider_response_status"] == "completed"


def test_fake_mode_debug_text_drives_the_exam_socket(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        session_id = str(create_session(client, "exam")["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            receive(socket, "call.started")
            socket.send_json({"type": "debug.transcript", "transcript": "Guten Tag."})
            receive(socket, "turn.completed")
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
    turn = container.repository.get_session(session_id).turns[0]
    assert (turn.user_text, turn.patient_text) == ("Guten Tag.", "Guten Tag.")


def test_realtime_instructions_brief_the_model_with_the_authored_case(
    container: Container,
) -> None:
    case = container.cases.list()[0]
    text = realtime_instructions(container.realtime_prompt, case)
    assert text.startswith(container.realtime_prompt.content)
    assert case.facts[0].patient_phrase in text
    assert case.unknown_response in text


CONTEXT = ExecutionContext(
    session_id="s",
    operation="speech_to_speech",
    case_version="1",
    case_hash="h",
    prompt_version="patient-realtime-v1",
    prompt_hash="p",
)


def test_openai_events_are_translated_and_timed() -> None:
    translator = RealtimeEventTranslator(CONTEXT, "gpt-realtime-mini")
    assert translator.translate(SimpleNamespace(type="session.updated")) is None
    speech = translator.translate(SimpleNamespace(type="input_audio_buffer.speech_started"))
    assert speech is not None and speech.type == "speech_started"
    transcript = translator.translate(
        SimpleNamespace(
            type="conversation.item.input_audio_transcription.completed",
            item_id="item_1",
            transcript="Seit wann?",
        )
    )
    assert transcript is not None and (transcript.item_id, transcript.text) == (
        "item_1",
        "Seit wann?",
    )
    failed = translator.translate(
        SimpleNamespace(type="conversation.item.input_audio_transcription.failed", item_id="item_2")
    )
    assert failed is not None and failed.type == "user_transcript" and failed.text == ""
    assert (
        translator.translate(
            SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp_1"))
        )
        is None
    )
    audio = translator.translate(
        SimpleNamespace(
            type="response.output_audio.delta",
            response_id="resp_1",
            delta=base64.b64encode(b"\x01\x02").decode(),
        )
    )
    assert audio is not None and audio.audio == b"\x01\x02" and audio.response_id == "resp_1"
    spoken = translator.translate(
        SimpleNamespace(
            type="response.output_audio_transcript.done",
            response_id="resp_1",
            transcript="Seit drei Tagen.",
        )
    )
    assert spoken is not None and spoken.text == "Seit drei Tagen."
    usage = SimpleNamespace(
        model_dump=lambda exclude_none: {"input_tokens": 12, "output_tokens": 5}
    )
    done = translator.translate(
        SimpleNamespace(
            type="response.done",
            response=SimpleNamespace(id="resp_1", status="completed", usage=usage),
        )
    )
    assert done is not None and done.completed and done.execution is not None
    assert done.execution.operation == "speech_to_speech"
    assert done.execution.status is ExecutionStatus.SUCCEEDED
    assert done.execution.provider_request_id == "resp_1"
    assert done.execution.usage == {
        "response_status": "completed",
        "input_tokens": 12,
        "output_tokens": 5,
    }
    cancelled = translator.translate(
        SimpleNamespace(
            type="response.done",
            response=SimpleNamespace(id="resp_2", status="cancelled", usage=None),
        )
    )
    assert cancelled is not None and not cancelled.completed
    assert cancelled.execution is not None
    assert cancelled.execution.status is ExecutionStatus.SUCCEEDED
    broken = translator.translate(
        SimpleNamespace(
            type="response.done",
            response=SimpleNamespace(id="resp_3", status="failed", usage=None),
        )
    )
    assert broken is not None and broken.execution is not None
    assert broken.execution.status is ExecutionStatus.FAILED
    assert broken.execution.error_code == "response_failed"
    notice = translator.translate(
        SimpleNamespace(type="error", error=SimpleNamespace(message="buffer too small"))
    )
    assert notice is not None and notice.type == "error" and notice.message == "buffer too small"


def test_openai_session_config_pins_pcm_server_vad_and_german_transcription() -> None:
    engine = OpenAIRealtimeEngine(
        "key",
        model="gpt-realtime-mini",
        voice="marin",
        transcription_model="gpt-4o-mini-transcribe",
        vad_silence_ms=900,
        connect_timeout_seconds=1,
    )
    config = engine.session_config("Sie sind der Patient.", "de")
    assert config["type"] == "realtime" and config["output_modalities"] == ["audio"]
    assert config["instructions"] == "Sie sind der Patient."
    audio = config["audio"]
    assert audio["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert audio["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert audio["output"]["voice"] == "marin"
    assert audio["input"]["transcription"] == {"model": "gpt-4o-mini-transcribe", "language": "de"}
    turn_detection = audio["input"]["turn_detection"]
    assert turn_detection["type"] == "server_vad"
    assert turn_detection["silence_duration_ms"] == 900
    assert turn_detection["interrupt_response"] is True
