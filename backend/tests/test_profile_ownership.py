from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from ari.api.app import create_app
from ari.container import Container
from ari.domain.models import CEFRLevel
from ari.infrastructure.persistence.identity import ProfileCredentialRow, ProfileCredentials


def test_profile_cookie_protects_history_session_goal_and_websocket(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as alice, TestClient(app) as bob, TestClient(app) as anonymous:
        created = alice.post("/api/learners", json={"target_cefr": "B2"})
        profile = created.json()
        assert "HttpOnly" in created.headers["set-cookie"]
        assert "SameSite=strict" in created.headers["set-cookie"]
        assert "token" not in profile
        assert alice.get("/api/profile").json()["id"] == profile["id"]
        assert alice.post("/api/learners", json={}).json()["id"] == profile["id"]
        other = bob.post("/api/learners", json={}).json()
        assert other["id"] != profile["id"]
        case = alice.get("/api/cases").json()[0]
        body = {"learner_id": profile["id"], "case_id": case["id"], "case_version": case["version"]}
        exercise = alice.post("/api/sessions", json=body).json()
        session_id = exercise["id"]
        assert alice.get(f"/api/learners/{profile['id']}/sessions").json()[0]["id"] == session_id
        for client in (bob, anonymous):
            for endpoint in (
                f"/api/learners/{profile['id']}/sessions",
                f"/api/learners/{profile['id']}/goal",
                f"/api/sessions/{session_id}",
                f"/api/sessions/{session_id}/vocabulary-hints",
            ):
                assert client.get(endpoint).status_code == 404
            assert client.post("/api/sessions", json=body).status_code == 404
            assert client.post(f"/api/sessions/{session_id}/end").status_code == 404
            assert (
                client.patch(
                    f"/api/learners/{profile['id']}/goal", json={"target_cefr": "C1"}
                ).status_code
                == 404
            )
            with (
                pytest.raises(WebSocketDisconnect) as closed,
                client.websocket_connect(f"/ws/sessions/{session_id}/voice"),
            ):
                pass
            assert closed.value.code == 1008
        assert alice.get(f"/api/sessions/{session_id}").status_code == 200
        with alice.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            socket.send_json({"type": "call.end"})
            assert socket.receive_json()["type"] == "call.ended"
        assert alice.get("/api/sessions/").status_code == 404
        with Session(container.repository.engine) as db:
            credentials = db.scalars(select(ProfileCredentialRow)).all()
            assert len(credentials) == 2
            assert all(c.token_hash != alice.cookies.get("ari_profile") for c in credentials)


def test_expiration_is_server_enforced_and_logout_revokes_replayed_cookie(
    container: Container,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    credentials = ProfileCredentials(container.repository.engine, clock=lambda: now)
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    token = credentials.issue(learner.id)
    assert credentials.resolve(token) == learner.id
    now += timedelta(days=365)
    assert credentials.resolve(token) is None
    with TestClient(create_app(container)) as owner, TestClient(create_app(container)) as replay:
        owner.post("/api/learners", json={})
        captured = owner.cookies.get("ari_profile")
        assert owner.delete("/api/profile").status_code == 204
        replay.cookies.set("ari_profile", captured)
        assert replay.get("/api/profile").status_code == 404


def test_stolen_profile_id_is_not_a_credential_and_cross_origin_mutation_is_rejected(
    container: Container,
) -> None:
    app = create_app(container)
    with TestClient(app) as owner, TestClient(app) as stranger:
        profile = owner.post("/api/learners", json={}).json()
        stranger.cookies.set("ari_profile", profile["id"])
        assert stranger.get("/api/profile").status_code == 404
        assert (
            owner.post(
                "/api/learners", json={}, headers={"Origin": "https://unrelated.invalid"}
            ).status_code
            == 404
        )
        assert owner.get("/api/profile").json()["id"] == profile["id"]


def test_production_cookie_is_secure(container: Container) -> None:
    services = replace(
        container,
        settings=container.settings.model_copy(
            update={
                "environment": "production",
            }
        ),
    )
    with TestClient(create_app(services), base_url="https://testserver") as client:
        response = client.post("/api/learners", json={})
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/profile").status_code == 200
