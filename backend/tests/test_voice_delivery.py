from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket

from ari.api.app import create_app
from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.application.services.voice import TurnBasedVoiceEngine
from ari.container import Container
from ari.domain.errors import ProviderError
from ari.domain.models import (
    ExecutionRecord,
    ExecutionStatus,
    new_id,
)
from ari.infrastructure.providers.fake import FakeSTTProvider, FakeTTSProvider


def create_session(client: TestClient) -> dict[str, object]:
    case = client.get("/api/cases").json()[0]
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
            socket.send_json(
                {"type": "debug.transcript", "transcript": "Seit wann haben Sie Schmerzen?"}
            )
            sent = receive(socket, "patient.audio_sent")
            before = client.get(f"/api/sessions/{session_id}").json()["turns"][0]
            assert "selected_fact_ids" not in before
            assert container.repository.get_session(session_id).turns[0].selected_fact_ids == (
                "symptom.onset",
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
            socket.send_json(
                {
                    "type": "audio.playback_started",
                    **ack,
                    "speech_end_to_audio_started_ms": 321,
                }
            )
            assert "revealed_fact_ids" not in receive(socket, "turn.audio_started")["turn"]
            assert container.repository.get_session(session_id).turns[0].revealed_fact_ids == ()
            socket.send_json({"type": "audio.playback_completed", **ack})
            first = receive(socket, "turn.delivered")["turn"]
            assert "revealed_fact_ids" not in first
            assert container.repository.get_session(session_id).turns[0].revealed_fact_ids == (
                "symptom.onset",
            )
            socket.send_json({"type": "audio.playback_completed", **ack})
            second = receive(socket, "turn.delivered")["turn"]
            assert first["audio_delivered_at"] == second["audio_delivered_at"]
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
        metric = container.repository.get_voice_turn_metric(str(sent["turn_id"]))
        assert metric is not None
        assert metric.speech_end_to_audio_started_ms == 321
        assert metric.clock_domains["speech_end_to_audio_started_ms"].value == "browser"
        assert metric.llm_total_ms is not None
        assert metric.tts_total_ms is not None
        aggregate = client.get("/api/technical/voice-metrics").json()
        assert aggregate["groups"][0]["voice_stack_id"] == "pipeline_economy"
        serialized = str(aggregate).casefold()
        assert "user_text" not in serialized
        assert "patient_text" not in serialized
        assert "learner_id" not in serialized
        assert "session_id" not in serialized
        assert "turn_id" not in serialized


def test_technical_metrics_endpoint_is_disabled_in_production(container: Container) -> None:
    production = replace(
        container,
        settings=container.settings.model_copy(update={"environment": "production"}),
    )
    with TestClient(create_app(production)) as client:
        assert client.get("/api/technical/voice-metrics").status_code == 404


def test_first_audio_sent_timestamp_includes_websocket_send_time(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_send = WebSocket.send_json

    async def delayed_send(self: WebSocket, data: Any, mode: str = "text") -> None:
        if isinstance(data, dict) and data.get("type") == "patient.audio_chunk":
            await asyncio.sleep(0.03)
        await original_send(self, data, mode=mode)

    monkeypatch.setattr(WebSocket, "send_json", delayed_send)
    with TestClient(create_app(container)) as client:
        session = create_session(client)
        with client.websocket_connect(f"/ws/sessions/{session['id']}/voice") as socket:
            socket.receive_json()
            socket.send_json({"type": "debug.transcript", "transcript": "Seit wann?"})
            completed = receive(socket, "turn.completed")
            metric = container.repository.get_voice_turn_metric(completed["turn"]["id"])
            assert metric is not None
            ready = datetime.fromisoformat(metric.wall_timestamps_utc["tts_first_byte_at"])
            sent = datetime.fromisoformat(metric.wall_timestamps_utc["first_audio_chunk_sent_at"])
            assert (sent - ready).total_seconds() >= 0.03
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
    services = replace(container, voice=TurnBasedVoiceEngine(FakeSTTProvider(), FragmentedPcmTTS()))
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
    services = replace(container, voice=TurnBasedVoiceEngine(FakeSTTProvider(), tts))
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
