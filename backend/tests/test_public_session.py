from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.api.voice_session_dto import public_session
from ari.container import Container


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


FORBIDDEN = {
    "selected_fact_ids",
    "revealed_fact_ids",
    "executions",
    "missed_fact_ids",
    "configs",
    "prompts",
}


def assert_public(value: Any) -> None:
    if isinstance(value, dict):
        assert not (FORBIDDEN & value.keys())
        for item in value.values():
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)


def new_session(client: TestClient) -> dict[str, Any]:
    case = client.get("/api/cases").json()[0]
    learner = client.post("/api/learners", json={"target_cefr": "C1"}).json()
    response = client.post(
        "/api/sessions",
        json={"learner_id": learner["id"], "case_id": case["id"], "case_version": case["version"]},
    )
    assert response.status_code == 201
    return response.json()


def test_public_session_allowlist_covers_http_and_websocket_payloads(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        created = new_session(client)
        session_id = created["id"]
        assert_public(created)
        assert created["evaluation"] is None
        assert created["vocabulary"] == []
        assert_public(client.get(f"/api/sessions/{session_id}").json())
        assert_public(client.get(f"/api/learners/{created['learner_id']}/sessions").json())

        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert_public(socket.receive_json())
            socket.send_json(
                {"type": "debug.transcript", "transcript": "Seit wann haben Sie Schmerzen?"}
            )
            audio_sent: dict[str, Any] | None = None
            while True:
                event = socket.receive_json()
                assert_public(event)
                if event["type"] == "patient.audio_sent":
                    audio_sent = event["data"]
                if event["type"] == "turn.completed":
                    break
            assert audio_sent is not None
            assert {"turn_id", "response_id", "audio_stream_id"} <= audio_sent.keys()
            socket.send_json({"type": "call.end"})
            assert_public(socket.receive_json())

        active = container.repository.get_session(session_id)
        sentinel_session = replace(
            active,
            training_snapshot={"scenario_id": "s", "configs": "private"},
            voice_stack_config={
                "transport": "pipeline",
                "models": {"tts": "fake"},
                "configs": "private",
            },
        )
        assert_public(public_session(sentinel_session))

        completed = client.post(f"/api/sessions/{session_id}/end", json={})
        assert completed.status_code == 200
        payload = completed.json()
        assert_public(payload)
        assert payload["evaluation"] is not None
        assert payload["metrics"] is not None
        assert payload["vocabulary"]
