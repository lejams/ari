from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from accounts_fixtures import PASSWORD, last_link, sign_in
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from ari.api.app import create_app
from ari.container import Container
from ari.domain.models import CEFRLevel
from ari.infrastructure.persistence.platform.account_rows import LearnerSessionRow
from ari.infrastructure.persistence.platform.identity import ProfileCredentials


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


def test_account_session_protects_history_session_goal_and_websocket(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as alice, TestClient(app) as bob, TestClient(app) as anonymous:
        assert anonymous.post("/api/learners", json={"target_cefr": "B2"}).status_code == 401
        sign_in(alice)
        sign_in(bob)
        profile = alice.post("/api/learners", json={"target_cefr": "B2"}).json()
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
        # Another account learns nothing (404); no session at all is asked to sign in (401).
        for client, refused in ((bob, 404), (anonymous, 401)):
            for endpoint in (
                f"/api/learners/{profile['id']}/sessions",
                f"/api/learners/{profile['id']}/goal",
                f"/api/sessions/{session_id}",
            ):
                assert client.get(endpoint).status_code == refused
            assert client.post("/api/sessions", json=body).status_code == refused
            assert client.post(f"/api/sessions/{session_id}/end").status_code == refused
            assert (
                client.patch(
                    f"/api/learners/{profile['id']}/goal", json={"target_cefr": "C1"}
                ).status_code
                == refused
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
            sessions = db.scalars(select(LearnerSessionRow)).all()
            assert len(sessions) == 2
            assert all(s.token_hash != alice.cookies.get("ari_session") for s in sessions)


def test_alpha_credential_expires_and_logout_revokes_replayed_session(
    container: Container,
) -> None:
    # The alpha profile credential is only read to adopt its learner, but still expires.
    now = datetime(2026, 1, 1, tzinfo=UTC)
    credentials = ProfileCredentials(container.repository.engine, clock=lambda: now)
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    token = credentials.issue(learner.id)
    assert credentials.resolve(token) == learner.id
    now += timedelta(days=365)
    assert credentials.resolve(token) is None
    with TestClient(create_app(container)) as owner, TestClient(create_app(container)) as replay:
        sign_in(owner)
        owner.post("/api/learners", json={})
        captured = owner.cookies.get("ari_session")
        assert owner.post("/api/auth/logout").status_code == 204
        replay.cookies.set("ari_session", captured)
        assert replay.get("/api/profile").status_code == 401
        assert replay.get("/api/auth/me").status_code == 401


def test_stolen_profile_id_is_not_a_credential_and_cross_origin_mutation_is_rejected(
    container: Container,
) -> None:
    app = create_app(container)
    with TestClient(app) as owner, TestClient(app) as stranger:
        sign_in(owner)
        profile = owner.post("/api/learners", json={}).json()
        for cookie in ("ari_session", "ari_profile"):
            stranger.cookies.set(cookie, profile["id"])
        assert stranger.get("/api/profile").status_code == 401
        assert (
            owner.post(
                "/api/learners", json={}, headers={"Origin": "https://unrelated.invalid"}
            ).status_code
            == 404
        )
        assert owner.get("/api/profile").json()["id"] == profile["id"]


def test_production_session_cookie_is_secure(container: Container) -> None:
    services = replace(
        container,
        settings=container.settings.model_copy(update={"environment": "production"}),
    )
    with TestClient(create_app(services), base_url="https://testserver") as client:
        client.post("/api/auth/signup", json={"email": "secure@example.test"})
        body = {"token": last_link(client, "secure@example.test"), "password": PASSWORD}
        response = client.post("/api/auth/password", json=body)
        assert "Secure" in response.headers["set-cookie"]
        client.post("/api/learners", json={})
        assert client.get("/api/profile").status_code == 200
