from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TypeVar

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ari.api.app import _is_websocket_disconnect_runtime, create_app
from ari.application.contracts import (
    AudioStreamEvent,
    ExecutionContext,
    LLMRequest,
    ProviderResult,
    TranscriptionConfig,
)
from ari.application.ports.stt import Transcription
from ari.application.prompting import load_prompt
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.application.services.patient import PatientSimulator
from ari.config import PROJECT_ROOT
from ari.container import Container
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id
from ari.infrastructure.providers.fake import FakeLLMProvider

T = TypeVar("T", bound=BaseModel)


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


def execution(
    context: ExecutionContext, operation: str, status: ExecutionStatus
) -> ExecutionRecord:
    return ExecutionRecord(
        id=new_id(),
        session_id=context.session_id,
        turn_id=context.turn_id,
        operation=operation,
        provider="failure-test",
        model="failure-test-v1",
        status=status,
        prompt_version=context.prompt_version,
        prompt_hash=context.prompt_hash,
        case_version=context.case_version,
        case_hash=context.case_hash,
        latency_ms=1,
        usage={"audio_seconds": 1.0} if operation == "speech_to_text" else {},
        error_code="forced_failure" if status is ExecutionStatus.FAILED else None,
        error_message="forced failure" if status is ExecutionStatus.FAILED else None,
    )


class FailingLLMProvider:
    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        del response_model
        raise ProviderError(
            "patient generation failed",
            execution=execution(request.context, request.context.operation, ExecutionStatus.FAILED),
        )


class ScriptedTranscriber:
    async def transcribe(
        self,
        pcm16: bytes,
        *,
        sample_rate: int,
        context: ExecutionContext,
        config: TranscriptionConfig,
    ) -> Transcription:
        del pcm16, sample_rate, config
        return Transcription(
            "Seit wann haben Sie Schmerzen?",
            execution(context, "speech_to_text", ExecutionStatus.SUCCEEDED),
        )


class FailingTTSProvider:
    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        del text
        raise ProviderError(
            "speech synthesis failed",
            execution=execution(context, "text_to_speech", ExecutionStatus.FAILED),
        )
        yield AudioStreamEvent("completed")  # pragma: no cover


def create_session(client: TestClient) -> dict[str, object]:
    case = client.get("/api/cases").json()[0]
    learner = client.post("/api/learners", json={"target_cefr": "C1"}).json()
    return client.post(
        "/api/sessions",
        json={
            "learner_id": learner["id"],
            "case_id": case["id"],
            "case_version": case["version"],
        },
    ).json()


def test_starlette_disconnect_runtime_is_recognized() -> None:
    assert _is_websocket_disconnect_runtime(
        RuntimeError('Cannot call "receive" once a disconnect message has been received.')
    )
    assert not _is_websocket_disconnect_runtime(RuntimeError("unexpected application failure"))


def test_http_and_websocket_vertical_slice(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        assert client.get("/api/learners/missing/goal").status_code == 404
        assert client.get("/api/health").json()["status"] == "ok"
        assert len(client.get("/api/cases").json()) == 1
        case = client.get("/api/cases").json()[0]
        learner = client.post("/api/learners", json={"target_cefr": "C1"}).json()
        session = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
            },
        ).json()

        with client.websocket_connect(f"/ws/sessions/{session['id']}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            socket.send_json(
                {"type": "debug.transcript", "transcript": "Seit wann haben Sie Schmerzen?"}
            )
            events: list[dict[str, object]] = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "turn.completed":
                    break
            socket.send_json({"type": "call.end"})
            assert socket.receive_json()["type"] == "call.ended"

        event_types = {str(event["type"]) for event in events}
        assert {
            "user.speech_started",
            "user.transcript_final",
            "patient.response_text",
            "patient.audio_chunk",
            "turn.completed",
        } <= event_types
        completed = client.post(f"/api/sessions/{session['id']}/end", json={})
        assert completed.status_code == 200
        payload = completed.json()
        assert payload["status"] == "completed"
        assert payload["evaluation"]["summary"]
        assert payload["metrics"]["pronunciation_status"] == "not_assessed"
        assert payload["turns"][0]["delivery_status"] == "unconfirmed"
        assert "revealed_fact_ids" not in payload["turns"][0]
        assert payload["turns"][0]["audio_delivered_at"] is None


def test_end_without_transcript_keeps_session_resumable(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        session = create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            ended = client.post(f"/api/sessions/{session_id}/end", json={})
            assert ended.status_code == 400

        persisted = client.get(f"/api/sessions/{session_id}").json()
        assert persisted["status"] == "active"
        assert persisted["turns"] == []
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as resumed:
            assert resumed.receive_json()["type"] == "call.started"
            resumed.send_json({"type": "call.end"})
            assert resumed.receive_json()["type"] == "call.ended"


def test_second_voice_connection_is_rejected(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        case = client.get("/api/cases").json()[0]
        learner = client.post("/api/learners", json={"target_cefr": "C1"}).json()
        session = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
            },
        ).json()

        with client.websocket_connect(f"/ws/sessions/{session['id']}/voice") as first:
            assert first.receive_json()["type"] == "call.started"
            with client.websocket_connect(f"/ws/sessions/{session['id']}/voice") as second:
                rejected = second.receive_json()
                assert rejected["type"] == "voice.error"
                assert "already active" in rejected["data"]["message"]
            first.send_json({"type": "call.end"})
            assert first.receive_json()["type"] == "call.ended"


def test_successful_stt_trace_survives_patient_failure(container: Container) -> None:
    orchestrator = ConversationOrchestrator(
        container.repository,
        container.cases,
        PatientSimulator(
            FailingLLMProvider(),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "patient_v2.txt",
                "patient-v2",
            ),
        ),
        LLMBackedEvaluator(
            FakeLLMProvider(),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "evaluation_v2.txt",
                "evaluation-v2",
            ),
            "fr-FR",
        ),
        container.voice_stack,
    )
    services = replace(container, orchestrator=orchestrator, transcriber=ScriptedTranscriber())
    app = create_app(services)

    with TestClient(app) as client:
        session = create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            events = [socket.receive_json()]
            socket.send_bytes(bytes(24_000))
            socket.send_json({"type": "user.turn.finish"})
            while events[-1]["type"] != "voice.error":
                events.append(socket.receive_json())

        persisted = client.get(f"/api/sessions/{session_id}").json()

    assert "executions" not in persisted
    records = {
        item.operation: jsonable_encoder(item)
        for item in services.repository.get_session(session_id).executions
    }
    assert records["speech_to_text"]["status"] == "succeeded"
    assert records["speech_to_text"]["turn_id"]
    assert records["patient_simulation"]["status"] == "failed"
    assert len(persisted["turns"]) == 1
    assert persisted["turns"][0]["user_text"] == "Seit wann haben Sie Schmerzen?"
    assert persisted["turns"][0]["patient_text"] == ""
    assert persisted["turns"][0]["provider_response_status"] == "failed"
    assert persisted["turns"][0]["response_state"] == "response_failed"


def test_tts_failure_keeps_persisted_turn_available_for_analysis(container: Container) -> None:
    services = replace(container, tts=FailingTTSProvider())
    app = create_app(services)

    with TestClient(app) as client:
        session = create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            socket.send_json(
                {"type": "debug.transcript", "transcript": "Erzählen Sie von fact-1."}
            )
            event_types: list[str] = []
            while not event_types or event_types[-1] != "voice.error":
                event_types.append(socket.receive_json()["type"])

        assert "turn.persisted" in event_types
        assert "turn.completed" not in event_types
        persisted = client.get(f"/api/sessions/{session_id}").json()
        assert len(persisted["turns"]) == 1
        assert persisted["turns"][0]["delivery_status"] == "failed"
        assert persisted["turns"][0]["response_state"] == "tts_failed"
        assert "selected_fact_ids" not in persisted["turns"][0]
        assert services.repository.get_session(session_id).turns[0].selected_fact_ids
        assert "revealed_fact_ids" not in persisted["turns"][0]
        assert services.repository.get_session(session_id).turns[0].revealed_fact_ids == ()
        assert persisted["turns"][0]["audio_delivered_at"] is None
        assert any(
            item["operation"] == "text_to_speech" and item["status"] == "failed"
            for item in jsonable_encoder(services.repository.get_session(session_id).executions)
        )

        completed = client.post(f"/api/sessions/{session_id}/end", json={}).json()
        assert completed["status"] == "completed"
