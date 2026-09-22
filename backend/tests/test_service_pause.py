from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ari.api.app import create_app
from ari.api.pause import PAUSED_MESSAGE
from ari.container import Container


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


def _paused(container: Container) -> Container:
    return replace(
        container,
        settings=container.settings.model_copy(update={"service_paused": True}),
    )


def test_pause_refuses_new_work_but_keeps_reading_and_closing(container: Container) -> None:
    # Set up a learner and a session on the live app, then reuse the cookie on the paused one.
    with TestClient(create_app(container)) as live:
        profile = live.post("/api/learners", json={"target_cefr": "B2"}).json()
        case = live.get("/api/cases").json()[0]
        body = {
            "learner_id": profile["id"],
            "case_id": case["id"],
            "case_version": case["version"],
        }
        session_id = live.post("/api/sessions", json=body).json()["id"]
        cookie = live.cookies.get("ari_profile")

    with TestClient(create_app(_paused(container))) as client:
        client.cookies.set("ari_profile", cookie)

        # Read-only stays available.
        assert client.get("/api/profile").status_code == 200
        assert client.get(f"/api/sessions/{session_id}").status_code == 200

        # New costed work is refused with the French message and Retry-After.
        for path in ("/api/sessions", "/api/practice/runs", "/api/placement/attempts"):
            refused = client.post(path, json=body)
            assert refused.status_code == 503, path
            assert refused.json()["detail"] == PAUSED_MESSAGE
            assert refused.headers["Retry-After"] == "3600"

        # Closing an existing session is not blocked.
        assert client.post(f"/api/sessions/{session_id}/end").status_code != 503

        # The voice socket is accepted, sent a displayable frame, then closed with 4503.
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            frame = socket.receive_json()
            assert frame["type"] == "voice.error"
            assert frame["data"]["message"] == PAUSED_MESSAGE
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
        assert closed.value.code == 4503


def test_pause_does_not_bypass_ownership_for_anonymous_callers(container: Container) -> None:
    with TestClient(create_app(_paused(container))) as anonymous:
        # Ownership runs before the pause guard, so a stranger still gets 404, not 503.
        assert anonymous.post("/api/sessions", json={}).status_code == 404
