"""Evaluation accounts (action 8, staging target of `voice-sim`).

An administrator marks a learner account from the server's CLI. That learner has no exam quota;
the sessions it starts are marked and stay out of the learner metrics, the weekly programme, the
lexicon and the Costs page. Every other learner keeps the quota.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from accounts_fixtures import sign_in
from clinical_fixtures import synthetic_bundle
from conftest import PROJECT_ROOT, publish_with_simulated_reviews
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_execution_costs import _execution
from test_usage_limits import NOW, FakeLedger, new_session, play_and_end

from ari.api.app import create_app
from ari.api.program import completed_since_week_start
from ari.application.services.usage_guard import UsageGuard, month_bounds
from ari.backoffice_api import cli
from ari.backoffice_api.container import Backoffice
from ari.config import Settings
from ari.container import Container, build_container
from ari.domain.errors import InvalidStateError, NotFoundError, QuotaExceededError
from ari.domain.models import (
    ConversationSession,
    LearnerProfile,
    LearningGoal,
    LearningMode,
    new_id,
)
from ari.infrastructure.persistence.platform.evaluation_accounts import EvaluationAccounts
from ari.infrastructure.persistence.platform.repository import LearnerRow, SqlSessionRepository
from ari.infrastructure.persistence.platform.usage import SqlUsageLedger
from ari.infrastructure.pricing import load_pricing


class EvaluationLedger(FakeLedger):
    def __init__(self, exams: int, *, evaluation: bool) -> None:
        super().__init__(exams=exams)
        self.evaluation = evaluation

    def is_evaluation_account(self, learner_id: str) -> bool:
        return self.evaluation


def guard(ledger: FakeLedger, *, ceiling: float | None = None) -> UsageGuard:
    return UsageGuard(
        ledger, exam_sessions_per_month=2, cost_ceiling_eur=ceiling, clock=lambda: NOW
    )


# ----- the guard, without a database ----------------------------------------------------


def test_an_evaluation_account_has_no_exam_quota_and_everyone_else_keeps_it() -> None:
    guard(EvaluationLedger(exams=50, evaluation=True)).check_session_start(
        "eval", LearningMode.EXAM
    )
    with pytest.raises(QuotaExceededError):
        guard(EvaluationLedger(exams=2, evaluation=False)).check_session_start(
            "learner", LearningMode.EXAM
        )


def test_the_optional_breaker_still_protects_an_evaluation_account() -> None:
    # Only the quota is lifted: an operator who sets a ceiling still sees it applied.
    ledger = EvaluationLedger(exams=0, evaluation=True)
    ledger.cost_micro_eur = 5_000_000
    with pytest.raises(Exception, match="limite de protection"):
        guard(ledger, ceiling=5.0).check_session_start("eval", LearningMode.EXAM)


# ----- marking, over the platform database ------------------------------------------------


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


def onboarded(client: TestClient) -> tuple[str, str]:
    email = sign_in(client)
    return email, str(client.post("/api/learners", json={}).json()["id"])


def finish(client: TestClient, session_id: str) -> None:
    """A training session with real turns, analysed: what feeds progression and lexicon."""
    with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
        assert socket.receive_json()["type"] == "call.started"
        for text in ("Guten Tag!", "Wo haben Sie Schmerzen?", "Seit wann?"):
            socket.send_json({"type": "debug.transcript", "transcript": text})
            while socket.receive_json()["type"] != "turn.completed":
                pass
        socket.send_json({"type": "call.end"})
        assert socket.receive_json()["type"] == "call.ended"
    response = client.post(f"/api/sessions/{session_id}/end")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


def test_marking_needs_an_onboarded_account_and_keeps_its_first_date(
    make_container: Any,
) -> None:
    container = make_container()
    accounts = EvaluationAccounts(container.repository.engine)
    with pytest.raises(NotFoundError):
        accounts.mark("nobody@example.test")
    with TestClient(create_app(container)) as client:
        early = sign_in(client)
        with pytest.raises(InvalidStateError, match="onboarding"):
            accounts.mark(early)
        client.post("/api/learners", json={})
    first = accounts.mark(early.upper())  # the address is normalised like at sign-up
    assert first.evaluation_since is not None
    assert accounts.mark(early).evaluation_since == first.evaluation_since
    assert accounts.list() == (first,)
    assert accounts.unmark(early).evaluation_since is None
    assert accounts.list() == ()


def test_an_evaluation_account_skips_the_quota_and_its_sessions_are_marked(
    make_container: Any,
) -> None:
    container = make_container(exam_sessions_per_month=1)
    accounts = EvaluationAccounts(container.repository.engine)
    with (
        TestClient(create_app(container)) as evaluator,
        TestClient(create_app(container)) as learner,
    ):
        email, evaluator_id = onboarded(evaluator)
        _, learner_id = onboarded(learner)
        accounts.mark(email)
        for _ in range(3):
            session = new_session(evaluator, evaluator_id, "exam")
            assert session.status_code == 201, session.text
            assert session.json()["evaluation_account"] is True
            play_and_end(evaluator, session.json()["id"])

        normal = new_session(learner, learner_id, "exam")
        assert normal.json()["evaluation_account"] is False
        play_and_end(learner, normal.json()["id"])
        assert new_session(learner, learner_id, "exam").status_code == 402

        # Unmarked, the account gets the quota back; its evaluation sessions never count.
        accounts.unmark(email)
        start, end = month_bounds(datetime.now(UTC))
        assert (
            SqlUsageLedger(container.repository.engine).exam_sessions_started(
                evaluator_id, start, end
            )
            == 0
        )
        again = new_session(evaluator, evaluator_id, "exam")
        assert again.json()["evaluation_account"] is False
        play_and_end(evaluator, again.json()["id"])
        assert new_session(evaluator, evaluator_id, "exam").status_code == 402


def test_evaluation_sessions_stay_out_of_programme_progression_and_lexicon(
    make_container: Any,
) -> None:
    container = make_container()
    accounts = EvaluationAccounts(container.repository.engine)
    with TestClient(create_app(container)) as client:
        email, learner_id = onboarded(client)
        accounts.mark(email)
        measured = new_session(client, learner_id, "training").json()["id"]
        finish(client, measured)

        now = datetime.now(UTC)
        assert completed_since_week_start(container, learner_id, now) == {}
        assert client.get("/api/progression").json()["axes"]["sessions_completed"] == 0
        assert container.lexicon.repository.list(learner_id) == ()
        assert client.get("/api/program").status_code == 200
        # The history still lists what was run.
        assert [item["id"] for item in client.get("/api/history").json()["items"]] == [measured]

        # The same learner unmarked: a new session counts again, the old one still does not.
        accounts.unmark(email)
        counted = new_session(client, learner_id, "training").json()["id"]
        finish(client, counted)
        assert len(completed_since_week_start(container, learner_id, now)["voice_training"]) == 1
        axes = client.get("/api/progression").json()["axes"]
        assert axes["sessions_completed"] == 1
        assert axes["lexicon"]["added_last_30_days"] >= 1  # the control does feed the lexicon


# ----- costs page ----------------------------------------------------------------------------


def test_evaluation_calls_are_reported_apart_from_learner_costs(
    backoffice: Backoffice, database_url: str
) -> None:
    repository = SqlSessionRepository(
        database_url, pricing=load_pricing(backoffice.settings.pricing_path)
    )
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    luna = round((0.10 + 0.50) * 0.88 * 1_000_000)

    def learner_with_session(*, evaluation: bool) -> tuple[str, str]:
        learner = repository.create_learner(LearnerProfile(id=new_id(), goal=LearningGoal()))
        session = repository.create_session(
            ConversationSession(
                id=new_id(),
                learner_id=learner.id,
                case_id="case",
                case_version="1",
                case_hash="hash",
                goal=LearningGoal(),
                evaluation_account=evaluation,
            )
        )
        repository.record_execution(_execution(session.id, "openai", "gpt-6-luna", usage))
        return learner.id, session.id

    real, real_session = learner_with_session(evaluation=False)
    # A session marked at creation stays excluded although its learner is no longer marked.
    learner_with_session(evaluation=True)
    repository.engine.dispose()

    report = backoffice.costs.summary(datetime(2000, 1, 1, tzinfo=UTC))["learners"]
    assert report["total"] == {"calls": 1, "known_micro_eur": luna, "unknown_calls": 0}
    assert [row["learner_id"] for row in report["by_learner"]] == [real]
    assert report["median_learner_micro_eur"] == luna
    assert [row["session_id"] for row in report["recent_sessions"]] == [real_session]
    assert [row["calls"] for row in report["by_day"]] == [1]
    assert report["evaluation_excluded"] == {
        "calls": 1,
        "known_micro_eur": luna,
        "unknown_calls": 0,
    }


def test_calls_without_a_session_of_a_marked_learner_are_excluded(
    backoffice: Backoffice, database_url: str
) -> None:
    repository = SqlSessionRepository(
        database_url, pricing=load_pricing(backoffice.settings.pricing_path)
    )
    learner = repository.create_learner(LearnerProfile(id=new_id(), goal=LearningGoal()))
    placement = _execution("unused", "openai", "gpt-6-luna", {"prompt_tokens": 10})
    repository.record_execution(
        replace(placement, session_id=None, learner_id=learner.id, attempt_ref="placement:a")
    )
    repository.engine.dispose()
    since = datetime(2000, 1, 1, tzinfo=UTC)
    assert backoffice.costs.summary(since)["learners"]["total"]["calls"] == 1
    with Session(backoffice.registry._store.engine) as db:
        db.execute(update(LearnerRow).values(evaluation_since=datetime.now(UTC)))
        db.commit()
    report = backoffice.costs.summary(since)["learners"]
    assert report["total"]["calls"] == 0
    assert report["by_learner"] == []
    assert report["evaluation_excluded"]["calls"] == 1


# ----- the administrator's command ----------------------------------------------------------


def test_the_cli_marks_lists_and_unmarks_an_account(
    make_container: Any,
    database_url: str,
    content_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = make_container()
    with TestClient(create_app(container)) as client:
        email, learner_id = onboarded(client)
    for name, value in {
        "ARI_ENVIRONMENT": "test",
        "ARI_PROVIDER_MODE": "fake",
        "ARI_DATABASE_URL": database_url,
        "ARI_CONTENT_DATABASE_URL": content_database_url,
    }.items():
        monkeypatch.setenv(name, value)

    assert cli.run(["evaluation-account", "mark", "--email", email]) == 0
    marked = json.loads(capsys.readouterr().out)
    assert marked["learner_id"] == learner_id and marked["evaluation_since"]
    assert container.repository.get_learner(learner_id).evaluation_account

    assert cli.run(["evaluation-account", "list"]) == 0
    assert [item["email"] for item in json.loads(capsys.readouterr().out)["items"]] == [email]

    assert cli.run(["evaluation-account", "unmark", "--email", email]) == 0
    assert json.loads(capsys.readouterr().out)["evaluation_since"] is None
    assert not container.repository.get_learner(learner_id).evaluation_account

    assert cli.run(["evaluation-account", "mark", "--email", "nobody@example.test"]) == 2
    assert "NotFoundError" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.run(["evaluation-account", "mark"])
