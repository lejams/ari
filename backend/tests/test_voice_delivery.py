from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from accounts_fixtures import sign_in
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.container import Container
from ari.domain.errors import ProviderError
from ari.domain.models import (
    ExecutionRecord,
    ExecutionStatus,
    new_id,
)
from ari.infrastructure.providers.fake import FAKE_TRANSCRIPT, FakeTTSProvider


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


def create_session(client: TestClient) -> dict[str, object]:
    case = client.get("/api/cases").json()[0]
    sign_in(client)
    learner = client.post("/api/learners", json={}).json()
    return client.post(
        "/api/sessions",
        json={"learner_id": learner["id"], "case_id": case["id"], "case_version": case["version"]},
    ).json()


def receive(socket: object, wanted: str) -> dict[str, object]:
    while True:
        event = socket.receive_json()  # type: ignore[attr-defined]
        if event["type"] == wanted:
            return event["data"]


def test_pipeline_delivery_is_correlated_idempotent_and_controls_fact_credit(
    container: Container,
) -> None:
    with TestClient(create_app(container)) as client:
        session = create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            socket.receive_json()
            socket.send_json({"type": "debug.transcript", "transcript": "Erzählen Sie von fact-1."})
            sent = receive(socket, "patient.audio_sent")
            before = client.get(f"/api/sessions/{session_id}").json()["turns"][0]
            assert "selected_fact_ids" not in before
            assert container.repository.get_session(session_id).turns[0].selected_fact_ids == (
                "fact-1",
            )
            assert "revealed_fact_ids" not in before
            assert container.repository.get_session(session_id).turns[0].revealed_fact_ids == ()
            ack = {
                "turn_id": sent["turn_id"],
                "response_id": sent["response_id"],
                "audio_stream_id": sent["audio_stream_id"],
                "last_index": sent["last_index"],
            }
            socket.send_json(
                {"type": "audio.playback_completed", **ack, "audio_stream_id": "wrong"}
            )
            receive(socket, "audio.ack_rejected")
            socket.send_json({"type": "audio.playback_completed", **ack})
            receive(socket, "audio.ack_rejected")
            socket.send_json({"type": "audio.playback_started", **ack})
            assert "revealed_fact_ids" not in receive(socket, "turn.audio_started")["turn"]
            assert container.repository.get_session(session_id).turns[0].revealed_fact_ids == ()
            socket.send_json({"type": "audio.playback_completed", **ack})
            first = receive(socket, "turn.delivered")["turn"]
            assert "revealed_fact_ids" not in first
            assert container.repository.get_session(session_id).turns[0].revealed_fact_ids == (
                "fact-1",
            )
            socket.send_json({"type": "audio.playback_completed", **ack})
            second = receive(socket, "turn.delivered")["turn"]
            assert first["audio_delivered_at"] == second["audio_delivered_at"]
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")


class FailOnceTTS:
    def __init__(self) -> None:
        self.calls = 0
        self.fallback = FakeTTSProvider()

    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            execution = ExecutionRecord(
                id=new_id(),
                session_id=context.session_id,
                turn_id=context.turn_id,
                operation="text_to_speech",
                provider="test",
                model="test-tts",
                status=ExecutionStatus.FAILED,
                prompt_version=None,
                prompt_hash=None,
                case_version=context.case_version,
                case_hash=context.case_hash,
                latency_ms=1,
                usage={},
                error_code="forced",
                error_message="forced",
                retryable=True,
            )
            raise ProviderError("forced", execution=execution)
        async for event in self.fallback.stream(text, context):
            yield event


class FragmentedPcmTTS:
    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        del text, context
        yield AudioStreamEvent("chunk", data=b"\x00")
        yield AudioStreamEvent("chunk", data=b"\x01\x02\x03")
        yield AudioStreamEvent("completed")


def test_pipeline_normalizes_odd_pcm_fragments_before_counting_chunks(
    container: Container,
) -> None:
    services = replace(container, tts=FragmentedPcmTTS())
    with TestClient(create_app(services)) as client:
        session = create_session(client)
        with client.websocket_connect(f"/ws/sessions/{session['id']}/voice") as socket:
            socket.receive_json()
            socket.send_json({"type": "debug.transcript", "transcript": "Seit wann?"})
            sent = receive(socket, "patient.audio_sent")
            assert sent["last_index"] == 0
            ack = {
                "turn_id": sent["turn_id"],
                "response_id": sent["response_id"],
                "audio_stream_id": sent["audio_stream_id"],
                "last_index": 0,
            }
            socket.send_json({"type": "audio.playback_started", **ack})
            receive(socket, "turn.audio_started")
            socket.send_json({"type": "audio.playback_completed", **ack})
            assert receive(socket, "turn.delivered")["turn"]["response_state"] == "audio_delivered"
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")


def test_tts_retry_reuses_transcript_and_turn(container: Container) -> None:
    tts = FailOnceTTS()
    services = replace(container, tts=tts)
    with TestClient(create_app(services)) as client:
        session = create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            socket.receive_json()
            socket.send_json(
                {"type": "debug.transcript", "transcript": "Seit wann haben Sie Schmerzen?"}
            )
            failed = receive(socket, "turn.tts_failed")["turn"]
            assert "revealed_fact_ids" not in failed
            assert services.repository.get_session(session_id).turns[0].revealed_fact_ids == ()
            socket.send_json({"type": "turn.retry_tts", "turn_id": failed["id"]})
            retried = receive(socket, "turn.retry_completed")["turn"]
            assert retried["audio_attempt"] == 2
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
        assert len(client.get(f"/api/sessions/{session_id}").json()["turns"]) == 1
        assert tts.calls == 2


def test_push_to_talk_transcribes_the_buffered_utterance(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        session = create_session(client)
        with client.websocket_connect(f"/ws/sessions/{session['id']}/voice") as socket:
            socket.receive_json()
            socket.send_json({"type": "user.turn.finish"})
            assert socket.receive_json()["type"] == "user.turn.empty"
            socket.send_bytes(b"Haben Sie Fieber?" + b" " * 12_000)
            assert socket.receive_json()["type"] == "user.speech_started"
            socket.send_json({"type": "user.turn.finish"})
            assert receive(socket, "user.transcript_final")["text"] == "Haben Sie Fieber?"
            receive(socket, "turn.completed")
            socket.send_bytes(bytes(24_000))
            socket.send_json({"type": "user.turn.finish"})
            assert receive(socket, "user.transcript_final")["text"] == FAKE_TRANSCRIPT
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
        stored = container.repository.get_session(str(session["id"]))
        assert [turn.user_text for turn in stored.turns] == ["Haben Sie Fieber?", FAKE_TRANSCRIPT]
        assert {e.operation for e in stored.executions} >= {"speech_to_text", "patient_simulation"}
