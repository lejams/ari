"""Progression by axis: deterministic aggregates over sessions, lexicon and placement."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from accounts_fixtures import sign_in
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.application.services.lexicon import LexiconOverview
from ari.application.services.progress_axes import PROGRESS_AXES_VERSION, progress_axes
from ari.container import Container
from ari.domain.models import (
    CEFRLevel,
    ConversationSession,
    Evaluation,
    EvidenceObservation,
    LearningGoal,
    LearningMode,
    SessionStatus,
)

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _session(index: int, *, category: str, verdict: str, covered: list[str]) -> ConversationSession:
    created = NOW - timedelta(days=10 - index)
    structure = {
        "version": "anamnesis-sections-v1",
        "sections": [
            {
                "id": "noxen",
                "label": "Noxen",
                "fact_ids": ["smoking"],
                "covered_fact_ids": covered,
                "missing_fact_ids": [],
            },
            {
                "id": "allergien",
                "label": "Allergien",
                "fact_ids": ["allergy"],
                "covered_fact_ids": ["allergy"],
                "missing_fact_ids": [],
            },
        ],
        "order_observed": ["allergien"],
        "canonical_order_respected": index % 2 == 0,
        "covered_count": 1,
        "total_count": 2,
    }
    evaluation = Evaluation(
        schema_version="session-evaluation-v4",
        prompt_version="t",
        rubric_version="r",
        overall_score=0,
        max_score=10,
        summary="",
        strengths=(),
        priorities=(),
        missed_fact_ids=(),
        language_errors=(EvidenceObservation("erreur", (1,), category),),
        criteria=(),
        structure=structure,
        empathy=({"moment_id": "m", "cue": "deuil", "verdict": verdict, "response_turn": 2},),
    )
    return ConversationSession(
        id=f"s{index}",
        learner_id="l",
        case_id="c",
        case_version="1",
        case_hash="h",
        goal=LearningGoal(),
        status=SessionStatus.COMPLETED,
        evaluation=evaluation,
        learning_mode=LearningMode.TRAINING,
        created_at=created,
        call_started_at=created,
        ended_at=created + timedelta(minutes=12),
    )


def test_axes_aggregate_sections_verdicts_and_recurring_error_categories() -> None:
    sessions = tuple(
        _session(
            i,
            category="case" if i % 3 else "gender",
            verdict="ignored" if i < 3 else "acknowledged",
            covered=["smoking"] if i > 4 else [],
        )
        for i in range(8)
    )
    overview = LexiconOverview(due=(), entries=(), by_state={})
    axes = progress_axes(sessions, (), overview, (), NOW)
    assert axes["version"] == PROGRESS_AXES_VERSION and axes["sessions_completed"] == 8
    structure = axes["structure"]
    assert [c["id"] for c in structure["columns"]] == ["allergien", "noxen"]  # canonical order
    assert structure["weakest"][0]["id"] == "noxen" and structure["weakest"][0]["ratio"] == 3 / 8
    assert structure["order_respected_rate"] == 0.5
    communication = axes["communication"]
    assert communication["counts"] == {"ignored": 3, "acknowledged": 5}
    # Last five moments all acknowledged; the five before were the three ignored ones.
    assert communication["acknowledged_rate"] == 1.0 and communication["previous_rate"] == 0.0
    language = axes["language"]
    assert language["sessions_considered"] == 5
    # Sessions 3..7: gender at 3 and 6, case elsewhere; both recur, most frequent first.
    assert language["by_category"] == {"case": 3, "gender": 2}
    assert [r["category"] for r in language["recurring"]] == ["case", "gender"]
    assert language["errors_per_session"] == 1.0
    regularity = axes["regularity"]
    assert len(regularity["weeks"]) == 4 and regularity["weeks"][-1]["voice_sessions"] >= 1
    assert regularity["weeks"][-1]["voice_minutes"] >= 12
    assert axes["level"] == {"history": [], "state": "estimated"}


def test_progression_endpoint_carries_axes_and_the_fake_categorises_errors(
    published_container: Container,
) -> None:
    app = create_app(published_container)
    with TestClient(app) as client:
        sign_in(client)
        client.post("/api/learners", json={"target_cefr": "C1"})
        empty = client.get("/api/progression").json()
        assert empty["axes"]["sessions_completed"] == 0
        assert empty["axes"]["lexicon"]["active"] == 0
        case = client.get("/api/cases").json()[0]
        learner = client.get("/api/profile").json()
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
            for text in ("Guten Tag!", "Wo haben Sie Schmerzen?", "Seit wann?"):
                socket.send_json({"type": "debug.transcript", "transcript": text})
                while socket.receive_json()["type"] != "turn.completed":
                    pass
            socket.send_json({"type": "call.end"})
            assert socket.receive_json()["type"] == "call.ended"
        done = client.post(f"/api/sessions/{session['id']}/end", json={}).json()
        assert done["evaluation"]["language_errors"][0]["category"] == "case"
        axes = client.get("/api/progression").json()["axes"]
        assert axes["sessions_completed"] == 1
        assert axes["language"]["by_category"] == {"case": 1}
        assert axes["lexicon"]["added_last_30_days"] >= 1
        # The stored evaluation keeps the category after a SQLite round trip.
        stored = published_container.repository.get_session(session["id"])
        assert stored.evaluation is not None
        assert stored.evaluation.language_errors[0].category == "case"


@pytest.mark.parametrize("level", [CEFRLevel.B1])
def test_helper_sessions_are_completed_with_evaluations(level: CEFRLevel) -> None:
    session = replace(
        _session(0, category="other", verdict="partial", covered=[]),
        goal=LearningGoal(target_cefr=level),
    )
    assert session.status is SessionStatus.COMPLETED and session.evaluation is not None
