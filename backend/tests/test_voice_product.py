from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from accounts_fixtures import sign_in
from clinical_fixtures import synthetic_bundle
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_clinical_registry import approve

from ari.api.app import create_app
from ari.application.services.assessment import weighted_assessment
from ari.application.services.progression import voice_progression
from ari.container import Container
from ari.domain.models import (
    AudioDeliveryStatus,
    ConversationTurn,
    Evaluation,
    LearningMode,
    SessionStatus,
)


@pytest.mark.parametrize("mode", ["training", "exam"])
def test_published_voice_mode_history_hints_and_immutability(
    practice_container: Container,
    mode: str,
) -> None:
    bundle = synthetic_bundle()
    store = practice_container.cases.store
    store.import_bundle(bundle)
    approve(store, bundle)
    with TestClient(create_app(practice_container)) as client:
        sign_in(client)
        learner = client.post("/api/learners", json={}).json()
        cases = client.get("/api/cases?approved_only=true").json()
        assert len(cases) == 1
        result = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": "SYNTHETIC-TEST",
                "case_version": "1",
                "scenario_id": "synthetic-scenario",
                "scenario_version": "1",
                "learning_mode": mode,
            },
        )
        assert result.status_code == 201
        run = result.json()
        assert run["learning_mode"] == mode
        session = practice_container.repository.get_session(run["id"])
        assert session.learning_mode is LearningMode(mode)
        path = f"/api/sessions/{run['id']}"
        history = client.get("/api/history").json()["items"]
        assert len(history) == 1 and history[0]["kind"] == "voice"
        assert history[0]["mode"] == mode and history[0]["feedback_state"] == "no_data"
        assert client.get("/api/progression").json()["groups"] == []
        store.withdraw("synthetic-scenario", "1", actor="ISOLATED TEST")
        assert client.get(path).status_code == 200
        assert client.get("/api/cases?approved_only=true").json() == []
        with pytest.raises(IntegrityError), practice_container.repository.engine.begin() as db:
            db.execute(
                text("UPDATE voice_learning_context SET mode=:mode"),
                {"mode": "training" if mode == "exam" else "exam"},
            )


def test_voice_progression_uses_weighted_evidence_and_separates_modes(
    practice_container: Container,
) -> None:
    from ari.domain.models import CEFRLevel

    bundle = synthetic_bundle()
    practice_container.cases.store.import_bundle(bundle)
    approve(practice_container.cases.store, bundle)
    learner = practice_container.orchestrator.create_learner(CEFRLevel.C1)
    case = practice_container.cases.get("SYNTHETIC-TEST", "1")
    session = practice_container.orchestrator.create_session(
        learner.id, case.id, case.version, learning_mode=LearningMode.EXAM
    )
    turn = ConversationTurn(
        id="synthetic-turn",
        session_id=session.id,
        sequence=1,
        user_text="Darf ich fragen?",
        patient_text="Seit 1 Tag.",
        revealed_fact_ids=("fact-1",),
        delivery_status=AudioDeliveryStatus.DELIVERED,
    )
    session = replace(session, turns=(turn,), status=SessionStatus.COMPLETED)
    criteria = weighted_assessment(session, case)
    evaluation = Evaluation(
        schema_version="session-evaluation-v2",
        prompt_version="test",
        rubric_version=case.rubric_version,
        overall_score=0,
        max_score=25,
        summary="ISOLATED TEST",
        strengths=(),
        priorities=(),
        missed_fact_ids=(),
        language_errors=(),
        criteria=tuple(criteria),
    )
    session = replace(session, evaluation=evaluation)
    progress = voice_progression(
        (session, replace(session, id="other", learning_mode=LearningMode.TRAINING)),
        practice_container.cases,
    )
    assert len(progress["groups"]) == 2
    dimension = progress["groups"][0]["points"][0]["dimensions"][0]
    assert dimension["score"] == 1.25  # 1/4 criterion weight, not 1/2 facts.
    assert dimension["evidence_turn_sequences"] == [1]
    assert dimension["state"] == "provisional"
    legacy = replace(
        session, evaluation=replace(evaluation, schema_version="session-evaluation-v1")
    )
    assert voice_progression((legacy,), practice_container.cases)["groups"] == []
    assert voice_progression((legacy,), practice_container.cases)["excluded"]
    wrong_rubric = replace(session, evaluation=replace(evaluation, rubric_version="unrelated@9"))
    assert voice_progression((wrong_rubric,), practice_container.cases)["groups"] == []
    assert (
        voice_progression((wrong_rubric,), practice_container.cases)["excluded"][0]["reason"]
        == "Version de rubrique incohérente"
    )


def test_concurrent_voice_start_retries_do_not_duplicate_or_change_mode(
    practice_container: Container,
) -> None:
    bundle = synthetic_bundle()
    practice_container.cases.store.import_bundle(bundle)
    approve(practice_container.cases.store, bundle)
    app = create_app(practice_container)
    with TestClient(app) as client:
        sign_in(client)
        learner = client.post("/api/learners", json={}).json()
        body = {
            "learner_id": learner["id"],
            "case_id": "SYNTHETIC-TEST",
            "case_version": "1",
            "learning_mode": "exam",
            "request_id": "identical-request",
        }
        cookie = client.cookies.get("ari_session")

        def start(_: int) -> str:
            with TestClient(app) as retry:
                retry.cookies.set("ari_session", cookie)
                response = retry.post("/api/sessions", json=body)
                assert response.status_code == 201
                return response.json()["id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(start, (0, 1)))
        assert ids[0] == ids[1]
        assert len(client.get("/api/history").json()["items"]) == 1
        assert (
            client.post("/api/sessions", json={**body, "learning_mode": "training"}).status_code
            == 400
        )
        practice_container.cases.store.withdraw("synthetic-scenario", "1", actor="ISOLATED TEST")
        assert client.post("/api/sessions", json=body).json()["id"] == ids[0]
