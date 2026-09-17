"""Learner model and weekly programme: deterministic rules, budget cap, recommendations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from clinical_fixtures import synthetic_bundle
from conftest import build_test_container, publish_with_simulated_reviews
from fastapi.testclient import TestClient
from practice_fixtures import synthetic_practice

from ari.api.app import create_app
from ari.application.services.learner_model import LearnerModel, SectionCoverage
from ari.application.services.program import (
    PROGRAM_RULES_VERSION,
    compatible,
    phase_for,
    plan_week,
    recommend_case,
    week_start,
)
from ari.container import Container
from ari.domain.models import CEFRLevel, LearnerDetails

TODAY = date(2026, 9, 17)  # a Thursday
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def model(**overrides: object) -> LearnerModel:
    base = LearnerModel(
        learner_id="l",
        declared_level="B1",
        estimated_level=None,
        estimated_at=None,
        exam_date=None,
        weeks_left=None,
        minutes_per_day=30,
        lexicon_total=5,
        lexicon_due=2,
        lexicon_by_state={},
        due_lemma_keys=frozenset({"schmerz"}),
        sections=(),
        sessions_considered=0,
        required_missed_sessions=0,
        empathy_counts={},
        voice_training_7d=0,
        voice_exam_7d=0,
        practice_7d=0,
        voice_minutes_7d=0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_phase_follows_the_reference_level_and_the_exam_horizon() -> None:
    assert phase_for(model(declared_level=None)) == "positionnement"
    assert phase_for(model(declared_level="A2")) == "prerequis"
    assert phase_for(model(declared_level="B1")) == "fondations"
    assert phase_for(model(declared_level="A2", estimated_level="B2")) == "anamnese"
    assert phase_for(model(estimated_level="B2", weeks_left=6)) == "examen"
    assert week_start(TODAY) == date(2026, 9, 14)


def test_week_plan_per_phase_and_budget(published_container: Container) -> None:
    cases = published_container.cases.list()
    practice = published_container.practice.catalog.list()

    positioning = plan_week(model(declared_level=None), cases, practice, TODAY, completed={})
    assert positioning["phase"] == "positionnement"
    assert positioning["slots"][0]["kind"] == "placement"
    assert not any(s["kind"].startswith("voice") for s in positioning["slots"])

    prerequisite = plan_week(model(declared_level="A2"), cases, practice, TODAY, completed={})
    assert "B2" in prerequisite["message"] and "vérifier" in prerequisite["message"]
    assert not any(s["kind"].startswith("voice") for s in prerequisite["slots"])
    assert sum(1 for s in prerequisite["slots"] if s["kind"] == "lexicon_review") == 7

    foundations = plan_week(model(declared_level="B1"), cases, practice, TODAY, completed={})
    voice = [s for s in foundations["slots"] if s["kind"] == "voice_training"]
    assert len(voice) == 2 and voice[0]["recommended"]["id"] == cases[0].id
    assert voice[0]["recommended"]["mode"] == "training"
    assert not any(s["kind"] == "voice_exam" for s in foundations["slots"])
    assert foundations["planned_minutes"] <= foundations["budget_minutes"]

    exam = plan_week(
        model(estimated_level="B2", weeks_left=4), cases, practice, TODAY, completed={}
    )
    assert exam["phase"] == "examen"
    assert any(
        s["kind"] == "voice_exam" and s["recommended"]["mode"] == "exam" for s in exam["slots"]
    )

    # Ten minutes a day cannot hold two voice sessions: lower priorities are trimmed first.
    tight = plan_week(
        model(declared_level="B1", minutes_per_day=10), cases, practice, TODAY, completed={}
    )
    assert tight["planned_minutes"] <= 70
    assert tight["trimmed_slot_ids"]
    assert (
        all(s["kind"] != "voice_training" for s in tight["slots"])
        or len([s for s in tight["slots"] if s["kind"] == "voice_training"]) < 2
    )
    assert sum(1 for s in tight["slots"] if s["kind"] == "lexicon_review") == 7


def test_slot_states_and_done_matching(published_container: Container) -> None:
    cases = published_container.cases.list()
    week = plan_week(
        model(declared_level="B1"),
        cases,
        (),
        TODAY,
        completed={
            "lexicon_review": [NOW - timedelta(days=2)],
            "voice_training": [NOW - timedelta(days=1)],
        },
    )
    lexicon = [s for s in week["slots"] if s["kind"] == "lexicon_review"]
    assert [s["state"] for s in lexicon] == [
        "done",
        "missed",
        "missed",
        "today",
        "todo",
        "todo",
        "todo",
    ]
    voice = [s for s in week["slots"] if s["kind"] == "voice_training"]
    assert [s["state"] for s in voice] == ["done", "todo"]
    assert week["next"]["kind"] == "lexicon_review" and week["next"]["state"] == "missed"
    assert week["version"] == PROGRAM_RULES_VERSION


def test_recommendation_prefers_due_words_weak_sections_and_level(
    published_container: Container,
) -> None:
    case = published_container.cases.list()[0]
    other = replace(case, id="OTHER", terminology=(), anamnesis_sections=(), cefr="C1")
    weak = (SectionCoverage("noxen", "Noxen", 0, 2),)
    chosen, reasons = recommend_case(
        model(declared_level="B1", due_lemma_keys=frozenset({"schmerz"}), sections=weak),
        (other, case),
        TODAY,
    )
    assert chosen is not None and chosen.id == case.id
    assert any("carnet" in r for r in reasons)
    # A C1 case is not compatible with a B1 learner unless nothing else exists.
    assert compatible(model(declared_level="B1"), other) is False
    only_other, _ = recommend_case(model(declared_level="B1"), (other,), TODAY)
    assert only_other is not None and only_other.id == "OTHER"


def test_program_api_reflects_profile_lexicon_and_activity(tmp_path) -> None:  # type: ignore[no-untyped-def]
    container = build_test_container(tmp_path / "program.db")
    publish_with_simulated_reviews(container.cases.store, synthetic_bundle())
    publish_with_simulated_reviews(container.cases.store, synthetic_practice("fachbegriffe").bundle)
    app = create_app(container)
    with TestClient(app) as client:
        client.post("/api/learners", json={"target_cefr": "C1"})
        first = client.get("/api/program").json()
        assert first["week"]["phase"] == "positionnement"
        assert first["model"]["level"]["reference"] is None

        me = client.get("/api/profile").json()
        client.patch(
            f"/api/learners/{me['id']}/profile",
            json={
                "declared_level": "B1",
                "minutes_per_day": 45,
                "exam_date": (TODAY + timedelta(weeks=20)).isoformat(),
            },
        )
        client.post("/api/lexicon/entries", json={"lemma": "Schmerz", "translation": "douleur"})
        second = client.get("/api/program").json()
        assert second["week"]["phase"] == "fondations"
        assert second["model"]["lexicon"]["due"] == 1
        kinds = {s["kind"] for s in second["week"]["slots"]}
        assert {"lexicon_review", "voice_training", "fachbegriffe"} <= kinds
        voice = next(s for s in second["week"]["slots"] if s["kind"] == "voice_training")
        assert "carnet" in voice["rationale"]  # the synthetic case teaches "Schmerz"
        fach = next(s for s in second["week"]["slots"] if s["kind"] == "fachbegriffe")
        assert fach["recommended"]["phase"] == "fachbegriffe"

        # A review today marks today's lexicon slot done and moves the next open slot along.
        entry = client.get("/api/lexicon").json()["entries"][0]
        client.post(
            f"/api/lexicon/entries/{entry['id']}/reviews", json={"event_id": "e1", "rating": "good"}
        )
        third = client.get("/api/program").json()
        today_slot = next(
            s
            for s in third["week"]["slots"]
            if s["kind"] == "lexicon_review" and s["date"] == third["week"]["today"]
        )
        assert today_slot["state"] == "done" or any(
            s["kind"] == "lexicon_review" and s["state"] == "done" for s in third["week"]["slots"]
        )
        # Estimated level takes precedence over the declared one.
        container.repository.update_learner(
            replace(
                container.repository.get_learner(me["id"]),
                details=replace(
                    container.repository.get_learner(me["id"]).details,
                    estimated_level=CEFRLevel.A2,
                    estimated_at=NOW,
                ),
            )
        )
        fourth = client.get("/api/program").json()
        assert fourth["week"]["phase"] == "prerequis"
        assert fourth["model"]["level"]["reference"] == "A2"


@pytest.mark.parametrize("level", ["B1", "B2"])
def test_details_defaults_keep_the_programme_computable(level: str) -> None:
    details = LearnerDetails(declared_level=CEFRLevel(level))
    assert details.minutes_per_day == 30 and details.estimated_level is None
