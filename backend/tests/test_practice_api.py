"""Offline product journey on synthetic data and isolated migrated databases only."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from accounts_fixtures import sign_in
from clinical_fixtures import simulated_review, synthetic_bundle
from fastapi.testclient import TestClient
from practice_fixtures import synthetic_practice
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ari.api.app import create_app
from ari.container import Container
from ari.domain.clinical import ClinicalBundle
from ari.domain.errors import InvalidStateError
from ari.domain.models import CEFRLevel, new_id, utc_now
from ari.domain.practice import PracticeRun
from ari.infrastructure.persistence.platform.practice import SqlPracticeRepository
from ari.infrastructure.persistence.platform.repository import SqlSessionRepository


@pytest.mark.parametrize("phase", ["arzt_arzt", "fachbegriffe"])
@pytest.mark.parametrize("mode", ["training", "exam"])
def test_onboard_answer_resume_feedback_history_progression(
    practice_container: Container,
    phase: str,
    mode: str,
) -> None:
    with TestClient(create_app(practice_container)) as client:
        assert client.get("/api/profile").status_code == 401
        sign_in(client)
        assert client.get("/api/profile").status_code == 404  # Signed in, not onboarded yet.
        learner = client.post("/api/learners", json={"target_cefr": "B2"}).json()
        assert learner["goal"]["target_cefr"] == "B2"
        assert client.get("/api/progression").json()["state"] == "no_data"
        catalog = client.get("/api/exercises").json()
        chosen = next(c for c in catalog["items"] if c["phase"] == phase)
        assert "accepted_answers" not in str(catalog)
        body = {
            "scenario_id": chosen["scenario_id"],
            "scenario_version": chosen["scenario_version"],
            "mode": mode,
            "request_id": new_id(),
        }
        run = client.post("/api/practice/runs", json=body).json()
        assert client.post("/api/practice/runs", json=body).json()["id"] == run["id"]
        assert (
            client.post(
                "/api/practice/runs",
                json={**body, "mode": "exam" if mode == "training" else "training"},
            ).status_code
            == 400
        )
        path = f"/api/practice/runs/{run['id']}"
        assert ("coaching_fr" in run["current_question"]) is (mode == "training")
        first = {
            "question_id": run["current_question"]["id"],
            "event_id": new_id(),
            "text": "Seit einem Tag.",
        }
        assert client.post(path + "/answers", json={**first, "text": "   "}).status_code == 422
        run = client.post(path + "/answers", json=first).json()
        assert len(client.post(path + "/answers", json=first).json()["answers"]) == 1
        assert (
            client.post(path + "/answers", json={**first, "text": "different"}).status_code == 400
        )
        assert ("training_feedback" in run) is (mode == "training")
        if mode == "exam":
            assert "accepted_answers" not in str(run)
            assert "coaching_fr" not in str(run)
        assert client.get("/api/progression").json()["groups"] == []
        assert client.post(path + "/pause").json()["status"] == "paused"
        second = {
            "question_id": "question-2",
            "event_id": new_id(),
            "text": "Das weiß ich nicht.",
        }
        assert client.post(path + "/answers", json=second).status_code == 400
        # Simulate a fresh application instance: state and identity persist in SQL.
        with TestClient(create_app(practice_container)) as reloaded:
            reloaded.cookies.update(client.cookies)
            assert reloaded.get(path).json()["status"] == "paused"
            assert reloaded.post(path + "/resume").json()["status"] == "active"
            assert reloaded.post(path + "/answers", json=second).status_code == 200
            finished = reloaded.post(path + "/finish").json()
        assert finished["status"] == "completed"
        feedback = finished["feedback"]
        assert feedback["state"] == "evaluated"
        assert feedback["dimensions"][0]["score"] == 5
        assert feedback["items"][0]["evidence"]["submitted_text"] == first["text"]
        assert feedback["next_step"] and feedback["limitations"]
        assert "overall_score" not in feedback
        if phase == "fachbegriffe":
            assert [d["id"] for d in feedback["dimensions"]] == ["lexical"]
        assert client.post(path + "/finish").json() == finished
        assert client.post(path + "/resume").status_code == 400
        assert (
            client.post(path + "/answers", json={**second, "event_id": new_id()}).status_code == 400
        )
        history = client.get("/api/history").json()["items"]
        assert len(history) == 1 and history[0]["id"] == run["id"]
        assert history[0]["mode"] == mode and history[0]["content"]["phase"] == phase
        assert history[0]["feedback_state"] == "evaluated"
        progress = client.get("/api/progression").json()
        assert len(progress["groups"]) == 1
        assert progress["groups"][0]["points"][0]["run_id"] == run["id"]


def test_owner_isolation_including_every_mutation(practice_container: Container) -> None:
    app = create_app(practice_container)
    with TestClient(app) as alice, TestClient(app) as bob, TestClient(app) as anon:
        sign_in(alice)
        alice.post("/api/learners", json={})
        sign_in(bob)
        bob.post("/api/learners", json={})
        run = alice.post(
            "/api/practice/runs",
            json={
                "scenario_id": "synthetic-arzt_arzt",
                "scenario_version": "1",
                "mode": "exam",
                "request_id": new_id(),
            },
        ).json()
        path = f"/api/practice/runs/{run['id']}"
        for stranger, refused in ((bob, 404), (anon, 401)):
            assert stranger.get(path).status_code == refused
            for action in ("pause", "resume", "finish"):
                assert stranger.post(path + "/" + action).status_code == refused
            assert (
                stranger.post(
                    path + "/answers",
                    json={
                        "question_id": "question-1",
                        "event_id": new_id(),
                        "text": "hijack",
                    },
                ).status_code
                == refused
            )
        assert bob.get("/api/history").json()["items"] == []
        assert bob.get("/api/progression").json()["groups"] == []
        assert anon.get("/api/history").status_code == 401
        assert anon.get("/api/progression").status_code == 401


def test_empty_catalog_refuses_unknown_exercises(container: Container) -> None:
    with TestClient(create_app(container)) as client:
        assert client.get("/api/exercises").json()["items"] == []
        sign_in(client)
        client.post("/api/learners", json={})
        assert (
            client.post(
                "/api/practice/runs",
                json={
                    "scenario_id": "synthetic-arzt_arzt",
                    "scenario_version": "1",
                    "mode": "training",
                    "request_id": new_id(),
                },
            ).status_code
            == 404
        )


def test_partial_and_empty_feedback_are_not_full_results(practice_container: Container) -> None:
    with TestClient(create_app(practice_container)) as client:
        sign_in(client)
        client.post("/api/learners", json={})
        for answered in (False, True):
            run = client.post(
                "/api/practice/runs",
                json={
                    "scenario_id": "synthetic-fachbegriffe",
                    "scenario_version": "1",
                    "mode": "exam",
                    "request_id": new_id(),
                },
            ).json()
            path = f"/api/practice/runs/{run['id']}"
            if answered:
                client.post(
                    path + "/answers",
                    json={"question_id": "question-1", "event_id": new_id(), "text": "oral"},
                )
            result = client.post(path + "/finish").json()["feedback"]
            assert result["state"] == ("provisional" if answered else "no_data")
            assert result["dimensions"][0]["score"] == (0 if answered else None)
            assert result["dimensions"][0]["answered_weight"] == (2 if answered else 0)
            assert result["dimensions"][0]["expected_weight"] == 3
            assert result["items"][-1]["evidence"] is None


def test_published_label_is_not_authorization(container: Container) -> None:
    learner = container.orchestrator.create_learner(CEFRLevel.B2)
    content = synthetic_practice("fachbegriffe")
    forged = PracticeRun(
        id=new_id(),
        learner_id=learner.id,
        request_id=new_id(),
        content=content.model_copy(update={"provenance": "published"}),
        mode="exam",
        created_at=utc_now(),
    )
    with pytest.raises(InvalidStateError, match="non publié"):
        container.practice.repository.create(forged)


def test_concurrent_retry_and_immutable_results(practice_container: Container) -> None:
    learner = practice_container.orchestrator.create_learner(CEFRLevel.C1)
    request_id = new_id()

    def start(_: int) -> str:
        return practice_container.practice.start(
            learner.id, "synthetic-fachbegriffe", "1", "exam", request_id
        ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(start, (1, 2)))
    assert ids[0] == ids[1]
    run_id = ids[0]
    event_id = new_id()

    def answer(_: int) -> int:
        return len(
            practice_container.practice.answer(
                learner.id, run_id, "question-1", "Seit 1 Tag.", event_id
            ).answers
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(answer, (1, 2))) == [1, 1]
    practice_container.practice.finish(learner.id, run_id)
    for sql in (
        "UPDATE practice_runs SET mode='training'",
        "UPDATE practice_runs SET content_hash='altered'",
        "UPDATE practice_runs SET status='active', feedback=NULL, ended_at=NULL",
        "UPDATE practice_answers SET event_id='altered'",
        "DELETE FROM practice_answers",
    ):
        with pytest.raises(IntegrityError), practice_container.repository.engine.begin() as db:
            db.execute(text(sql))
    # A separate engine/process reads the same pinned results and identity.
    fresh = SqlSessionRepository(practice_container.settings.database_url)
    assert SqlPracticeRepository(fresh.engine).get(run_id, learner.id).status == "completed"


def test_compatibility_separates_versions_modes_and_phases(practice_container: Container) -> None:
    learner = practice_container.orchestrator.create_learner(CEFRLevel.B2)
    for phase, mode in (
        ("arzt_arzt", "exam"),
        ("fachbegriffe", "exam"),
        ("fachbegriffe", "training"),
    ):
        run = practice_container.practice.start(
            learner.id, f"synthetic-{phase}", "1", mode, new_id()
        )
        practice_container.practice.finish(learner.id, run.id)
    from ari.application.services.progression import practice_progression

    series = practice_progression(practice_container.practice.repository.list(learner.id))["groups"]
    assert len(series) == 3
    assert {s["kind"] for s in series} == {"practice"}


@pytest.mark.parametrize("phase", ["arzt_arzt", "fachbegriffe"])
def test_future_published_content_uses_exact_reviewed_bundle_without_code_changes(
    container: Container,
    phase: str,
) -> None:
    # Synthetic TEST approvals in a disposable database; never real human reviews.
    content = synthetic_practice(phase)
    bundle = content.bundle
    scenario = bundle.scenarios[0]
    store = container.cases.store
    store.import_bundle(bundle)
    catalog = container.practice.catalog
    assert not any(c.scenario_id == scenario.id for c in catalog.list())
    with pytest.raises(InvalidStateError, match="Approbation humaine"):
        store.publish(scenario.id, scenario.version, actor="ISOLATED TEST")
    store.record_review(simulated_review(bundle, "clinical"))
    with pytest.raises(InvalidStateError, match="linguistic"):
        store.publish(scenario.id, scenario.version, actor="ISOLATED TEST")
    store.record_review(simulated_review(bundle, "linguistic"))
    store.publish(scenario.id, scenario.version, actor="ISOLATED TEST")
    published = catalog.get(scenario.id, scenario.version)
    assert published.provenance == "published"
    assert published.bundle.content_hash == bundle.content_hash
    learner = container.orchestrator.create_learner(CEFRLevel.B2)
    run = container.practice.start(learner.id, scenario.id, scenario.version, "exam", new_id())
    container.practice.answer(learner.id, run.id, "question-1", "Seit 1 Tag.", new_id())
    container.practice.answer(learner.id, run.id, "question-2", "Das weiß ich nicht.", new_id())
    store.withdraw(scenario.id, scenario.version, actor="ISOLATED TEST")
    # Withdrawal closes new starts, not a pinned learner's in-flight exercise or feedback.
    with pytest.raises(Exception, match="Aucun exercice approuvé"):
        container.practice.start(learner.id, scenario.id, scenario.version, "exam", new_id())
    assert (
        container.practice.start(
            learner.id, scenario.id, scenario.version, "exam", run.request_id
        ).id
        == run.id
    )
    finished = container.practice.finish(learner.id, run.id)
    assert finished.feedback is not None and finished.feedback.state == "evaluated"
    assert finished.content.content_hash == run.content.content_hash


def test_new_phase_without_specification_remains_unpublishable(
    practice_container: Container,
) -> None:
    raw = synthetic_bundle().model_dump(mode="json")
    raw["scenarios"][0]["phase"] = "arzt_arzt"
    bundle = ClinicalBundle.model_validate_json(json.dumps(raw))
    store = practice_container.cases.store
    store.import_bundle(bundle)
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))
    with pytest.raises(InvalidStateError, match="Phase modélisée"):
        store.publish(bundle.scenarios[0].id, "1", actor="ISOLATED TEST")


def test_forged_feedback_cannot_be_sealed(practice_container: Container) -> None:
    from pydantic import ValidationError

    from ari.domain.practice import assess_practice

    learner = practice_container.orchestrator.create_learner(CEFRLevel.B2)
    run = practice_container.practice.start(
        learner.id, "synthetic-fachbegriffe", "1", "exam", new_id()
    )
    feedback = assess_practice(run.content, run.answers)
    forged = feedback.model_copy(update={"next_step": "FAKE medical recommendation"})
    with pytest.raises(ValidationError, match="Feedback incohérent"):
        practice_container.practice.repository.finish(run.id, learner.id, forged, 0)
    assert practice_container.practice.repository.get(run.id, learner.id).status == "active"
