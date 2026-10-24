"""Structural usage limits: exam quota, one live voice session, exam duration, idle, breaker.

None of them rations a normal learner by cost; the optional breaker is off unless configured.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from accounts_fixtures import sign_in
from clinical_fixtures import synthetic_bundle
from conftest import PROJECT_ROOT, publish_with_simulated_reviews
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_execution_costs import _execution

from ari.api.app import create_app
from ari.application.ports.usage import MonthCost
from ari.application.services.usage_guard import UsageGuard, month_bounds
from ari.config import Settings
from ari.container import Container, build_container
from ari.domain.errors import QuotaExceededError, UsageCeilingError
from ari.domain.models import LearningMode
from ari.infrastructure.persistence.platform.repository import SessionRow

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class FakeLedger:
    def __init__(self, exams: int = 0, cost_micro_eur: int = 0) -> None:
        self.exams, self.cost_micro_eur = exams, cost_micro_eur
        self.windows: list[tuple[datetime, datetime]] = []

    def exam_sessions_started(self, learner_id: str, start: datetime, end: datetime) -> int:
        self.windows.append((start, end))
        return self.exams

    def is_evaluation_account(self, learner_id: str) -> bool:
        return False

    def cost(self, learner_id: str, start: datetime, end: datetime) -> MonthCost:
        return MonthCost(self.cost_micro_eur, 0)


def guard(ledger: FakeLedger, *, ceiling: float | None = None) -> UsageGuard:
    return UsageGuard(
        ledger, exam_sessions_per_month=2, cost_ceiling_eur=ceiling, clock=lambda: NOW
    )


# ----- the guard, without a database ----------------------------------------------------


def test_month_bounds_are_the_calendar_month_and_roll_over_the_year() -> None:
    assert month_bounds(NOW) == (
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    december = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
    assert month_bounds(december)[1] == datetime(2027, 1, 1, tzinfo=UTC)


def test_the_third_exam_of_the_month_is_refused_with_its_reset_date() -> None:
    ledger = FakeLedger(exams=2)
    with pytest.raises(QuotaExceededError, match="01/11/2026"):
        guard(ledger).check_session_start("learner", LearningMode.EXAM)
    assert ledger.windows == [month_bounds(NOW)]
    guard(FakeLedger(exams=1)).check_session_start("learner", LearningMode.EXAM)


def test_training_and_unmeasured_modes_are_never_limited_by_the_quota() -> None:
    busy = guard(FakeLedger(exams=99, cost_micro_eur=10**12))
    busy.check_session_start("learner", LearningMode.TRAINING)
    busy.check_session_start("learner", None)
    busy.check_new_activity("learner")


def test_the_cost_breaker_only_exists_when_a_ceiling_is_configured() -> None:
    expensive = FakeLedger(cost_micro_eur=5_000_000)
    guard(expensive).check_new_activity("learner")  # no ceiling set: nothing is refused
    with pytest.raises(UsageCeilingError, match="01/11/2026"):
        guard(expensive, ceiling=5.0).check_new_activity("learner")
    guard(FakeLedger(cost_micro_eur=4_999_999), ceiling=5.0).check_new_activity("learner")


# ----- over HTTP and WebSocket ----------------------------------------------------------


@pytest.fixture
def make_container(database_url: str) -> Iterator[Any]:
    built: list[Container] = []

    def make(**overrides: Any) -> Container:
        settings = Settings(
            _env_file=None,
            environment="test",
            provider_mode="fake",
            database_url=database_url,
            prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
            **overrides,
        )
        container = build_container(settings)
        publish_with_simulated_reviews(container.cases.store, synthetic_bundle())
        built.append(container)
        return container

    yield make
    for container in built:
        container.repository.engine.dispose()


def new_session(client: TestClient, learner_id: str, mode: str) -> Any:
    case = client.get("/api/cases").json()[0]
    return client.post(
        "/api/sessions",
        json={
            "learner_id": learner_id,
            "case_id": case["id"],
            "case_version": case["version"],
            "learning_mode": mode,
        },
    )


def learner(client: TestClient) -> str:
    sign_in(client)
    return str(client.post("/api/learners", json={}).json()["id"])


def receive(socket: Any, wanted: str) -> dict[str, Any]:
    while True:
        event = socket.receive_json()
        if event["type"] == wanted:
            return dict(event["data"])


def refused(socket: Any) -> tuple[dict[str, Any], int]:
    event = socket.receive_json()
    assert event["type"] == "voice.error", event
    closing = socket.receive()
    assert closing["type"] == "websocket.close", closing
    return dict(event["data"]), int(closing["code"])


def play_and_end(client: TestClient, session_id: str) -> None:
    with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
        receive(socket, "call.started")
        socket.send_json({"type": "call.end"})
        receive(socket, "call.ended")


def test_exam_quota_counts_calls_that_began_and_never_blocks_training(
    make_container: Any,
) -> None:
    container = make_container(exam_sessions_per_month=1)
    with TestClient(create_app(container)) as client:
        learner_id = learner(client)
        first = new_session(client, learner_id, "exam")
        assert first.status_code == 201
        # Creating is free: nothing has been spent until the call begins.
        assert new_session(client, learner_id, "exam").status_code == 201
        play_and_end(client, first.json()["id"])

        over = new_session(client, learner_id, "exam")
        assert over.status_code == 402
        assert "se renouvelle le 01/" in over.json()["detail"]
        training = new_session(client, learner_id, "training")
        assert training.status_code == 201


def test_a_session_created_before_the_quota_ran_out_is_refused_when_it_opens(
    make_container: Any,
) -> None:
    container = make_container(exam_sessions_per_month=1)
    with TestClient(create_app(container)) as client:
        learner_id = learner(client)
        first = new_session(client, learner_id, "exam").json()["id"]
        second = new_session(client, learner_id, "exam").json()["id"]
        play_and_end(client, first)
        with client.websocket_connect(f"/ws/sessions/{second}/voice") as socket:
            data, code = refused(socket)
    assert code == 4402 and "quota" in data["message"].lower()
    assert container.repository.get_session(second).call_started_at is None


def test_one_live_voice_session_per_learner(make_container: Any) -> None:
    container = make_container()
    with TestClient(create_app(container)) as client:
        learner_id = learner(client)
        first = new_session(client, learner_id, "training").json()["id"]
        second = new_session(client, learner_id, "training").json()["id"]
        with client.websocket_connect(f"/ws/sessions/{first}/voice") as live:
            receive(live, "call.started")
            with client.websocket_connect(f"/ws/sessions/{second}/voice") as blocked:
                data, code = refused(blocked)
            assert code == 4409 and "déjà ouverte" in data["message"]
            live.send_json({"type": "call.end"})
            receive(live, "call.ended")
        # The first connection is gone: the learner can open the other session.
        play_and_end(client, second)


def test_an_exam_ends_when_its_time_is_up_but_training_is_never_cut(
    make_container: Any,
) -> None:
    container = make_container()
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    with TestClient(create_app(container)) as client:
        learner_id = learner(client)
        exam = new_session(client, learner_id, "exam").json()["id"]
        training = new_session(client, learner_id, "training").json()["id"]
        for session_id in (exam, training):
            container.orchestrator.activate(session_id)
            with Session(container.repository.engine) as db, db.begin():
                db.execute(
                    update(SessionRow)
                    .where(SessionRow.id == session_id)
                    .values(call_started_at=long_ago)
                )

        with client.websocket_connect(f"/ws/sessions/{exam}/voice") as socket:
            receive(socket, "call.started")
            limit = receive(socket, "session.time_limit")
            receive(socket, "call.ended")
        assert limit["reason"] == "duration"

        with client.websocket_connect(f"/ws/sessions/{training}/voice") as socket:
            receive(socket, "call.started")
            socket.send_json({"type": "debug.transcript", "transcript": "Guten Tag."})
            receive(socket, "turn.completed")
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
    assert container.repository.get_session(training).turns


def test_the_analysis_still_runs_after_a_time_limit(make_container: Any) -> None:
    container = make_container()
    with TestClient(create_app(container)) as client:
        learner_id = learner(client)
        exam = new_session(client, learner_id, "exam").json()["id"]
        with client.websocket_connect(f"/ws/sessions/{exam}/voice") as socket:
            receive(socket, "call.started")
            socket.send_json({"type": "debug.transcript", "transcript": "Guten Tag."})
            receive(socket, "turn.completed")
        with Session(container.repository.engine) as db, db.begin():
            db.execute(
                update(SessionRow)
                .where(SessionRow.id == exam)
                .values(call_started_at=datetime.now(UTC) - timedelta(hours=2))
            )
        with client.websocket_connect(f"/ws/sessions/{exam}/voice") as socket:
            receive(socket, "call.started")
            receive(socket, "session.time_limit")
            receive(socket, "call.ended")
        # The browser answers the limit with its normal end request, which analyses.
        ended = client.post(f"/api/sessions/{exam}/end", json={}).json()
    assert ended["status"] == "completed" and ended["turns"]


def test_a_connection_without_activity_is_closed(
    make_container: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = make_container()
    monkeypatch.setattr(container.settings, "voice_idle_timeout_seconds", 1)
    with TestClient(create_app(container)) as client:
        learner_id = learner(client)
        session_id = new_session(client, learner_id, "training").json()["id"]
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            receive(socket, "call.started")
            limit = receive(socket, "session.time_limit")
            receive(socket, "call.ended")
        assert limit["reason"] == "idle"
        # The registry released the learner: a new connection is accepted.
        play_and_end(client, session_id)


@pytest.mark.parametrize(("ceiling", "expected"), [(0.10, 429), (None, 201)])
def test_the_breaker_refuses_new_activity_only_when_configured(
    make_container: Any, ceiling: float | None, expected: int
) -> None:
    spent = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}  # about 0.53 EUR
    container = make_container(learner_cost_ceiling_eur=ceiling)
    with TestClient(create_app(container)) as client:
        learner_id = learner(client)
        case = client.get("/api/cases").json()[0]
        seeded = container.orchestrator.create_session(learner_id, case["id"], case["version"])
        container.repository.record_execution(_execution(seeded.id, "openai", "gpt-6-luna", spent))
        assert new_session(client, learner_id, "training").status_code == expected
        if ceiling is not None:
            assert (
                client.post("/api/placement/attempts", json={"request_id": "req-1"}).status_code
                == 429
            )
            # Reading is never refused: history, profile and progress stay available.
            assert client.get("/api/profile").status_code == 200


def test_settings_default_to_no_euro_ceiling_and_read_an_empty_variable_as_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).learner_cost_ceiling_eur is None
    monkeypatch.setenv("ARI_LEARNER_COST_CEILING_EUR", "")
    assert Settings(_env_file=None).learner_cost_ceiling_eur is None
    monkeypatch.setenv("ARI_LEARNER_COST_CEILING_EUR", "12.5")
    assert Settings(_env_file=None).learner_cost_ceiling_eur == 12.5
