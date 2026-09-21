"""The personal lexicon closes the loop: sessions feed it, reviews and later sessions promote it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.container import Container
from ari.domain.errors import NotFoundError
from ari.domain.models import (
    CEFRLevel,
    LexiconSource,
    PatientResponseKind,
    SessionStatus,
    SrsRating,
    VocabularyState,
)

FRENCH_QUESTION = "Où avez-vous la douleur ?"


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


async def _completed_session(container: Container, learner_id: str, utterances: list[str]) -> str:
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(learner_id, case.id, case.version)
    container.orchestrator.activate(session.id)
    for text in utterances:
        await container.orchestrator.process_transcript(session.id, text)
    await container.orchestrator.end_session(session.id)
    return session.id


@pytest.mark.asyncio
async def test_french_utterance_is_answered_as_wrong_language_without_facts(
    container: Container,
) -> None:
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(learner.id, case.id, case.version)
    container.orchestrator.activate(session.id)

    outcome = await container.orchestrator.process_transcript(session.id, FRENCH_QUESTION)

    assert outcome.turn.patient_response_kind == PatientResponseKind.WRONG_LANGUAGE.value
    assert outcome.turn.patient_text == case.out_of_scope_response
    assert outcome.turn.selected_fact_ids == ()
    german = await container.orchestrator.process_transcript(session.id, "Haben Sie Fieber?")
    assert german.turn.patient_response_kind == PatientResponseKind.UNKNOWN.value


@pytest.mark.asyncio
async def test_session_feeds_the_lexicon_and_reanalysis_never_duplicates(
    container: Container,
) -> None:
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    session_id = await _completed_session(
        container, learner.id, ["Guten Tag!", "Was ist mit fact-1?", FRENCH_QUESTION]
    )

    overview = container.lexicon.overview(learner.id)
    by_lemma = {entry.lemma_key: entry for entry in overview.entries}
    # The evaluator candidate, the unused case term and the code switch all land once.
    assert by_lemma["ausstrahlen"].source is LexiconSource.EVALUATION_CANDIDATE
    assert by_lemma["schmerz"].source is LexiconSource.TERMINOLOGY_UNUSED
    assert all(entry.state is VocabularyState.IDENTIFIED for entry in overview.entries)
    assert len(overview.due) == len(overview.entries) == 2
    report = container.lexicon.repository.get_report(session_id)
    assert report is not None
    assert {entry.lemma_key for entry in report.added} == {"ausstrahlen", "schmerz"}
    assert report.promoted == ()
    assert report.wrong_language_turns == (3,)
    session = container.repository.get_session(session_id)
    assert session.evaluation is not None
    assert session.evaluation.code_switches[0].turn == 3
    assert session.evaluation.code_switches[0].intended_term == "Schmerz"

    # A re-analysis (e.g. after a failure) touches the same entries.
    container.repository.set_status(session_id, SessionStatus.ANALYSIS_FAILED)
    await container.orchestrator.end_session(session_id)
    again = container.lexicon.overview(learner.id)
    assert len(again.entries) == 2
    replayed = container.lexicon.repository.get_report(session_id)
    assert replayed is not None
    assert {entry.lemma_key for entry in replayed.added} == {"ausstrahlen", "schmerz"}


@pytest.mark.asyncio
async def test_short_sessions_do_not_flood_the_lexicon_with_unused_terms(
    container: Container,
) -> None:
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    await _completed_session(container, learner.id, ["Guten Tag!"])

    keys = {entry.lemma_key for entry in container.lexicon.overview(learner.id).entries}
    assert "schmerz" not in keys
    assert "ausstrahlen" in keys


@pytest.mark.asyncio
async def test_term_spoken_in_a_later_session_is_promoted_then_mastered(
    container: Container,
) -> None:
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    first = await _completed_session(
        container, learner.id, ["Guten Tag!", "Was ist mit fact-1?", "Haben Sie Fieber?"]
    )
    entry = next(
        e for e in container.lexicon.overview(learner.id).entries if e.lemma_key == "schmerz"
    )
    assert entry.state is VocabularyState.IDENTIFIED and entry.first_session_id == first

    second = await _completed_session(
        container, learner.id, ["Guten Tag!", "Haben Sie Schmerzen?", "Seit wann?"]
    )
    entry = container.lexicon.repository.get(learner.id, entry.id)
    assert entry.state is VocabularyState.USED
    assert entry.used_session_ids == (second,)
    report = container.lexicon.repository.get_report(second)
    assert report is not None
    assert [e.lemma_key for e in report.promoted] == ["schmerz"]

    third = await _completed_session(container, learner.id, ["Wo haben Sie Schmerzen?"])
    entry = container.lexicon.repository.get(learner.id, entry.id)
    assert entry.used_session_ids == (second, third)
    assert entry.state is VocabularyState.USED  # two sessions, but no successful review yet

    for event in ("r1", "r2"):
        entry = container.lexicon.review(learner.id, entry.id, event, SrsRating.GOOD)
        assert entry.state is VocabularyState.USED
    entry = container.lexicon.review(learner.id, entry.id, "r3", SrsRating.GOOD, maintenance_days=7)
    assert entry.state is VocabularyState.MASTERED
    # Acquired: the word leaves the SM-2 ladder and comes back at the maintenance cadence.
    assert entry.srs.interval_days == 7
    assert (entry.srs.due_at - entry.srs.last_reviewed_at).days == 7  # type: ignore[operator]
    overview = container.lexicon.overview(learner.id)
    assert [e.lemma_key for e in overview.acquired] == ["schmerz"]
    assert "schmerz" not in {e.lemma_key for e in overview.active}
    kept = container.lexicon.review(learner.id, entry.id, "r4", SrsRating.GOOD, maintenance_days=30)
    assert kept.state is VocabularyState.MASTERED and kept.srs.interval_days == 30
    assert kept.srs.repetitions == entry.srs.repetitions + 1
    # A miss during maintenance sends the word back to the active zone.
    demoted = container.lexicon.review(learner.id, entry.id, "r5", SrsRating.AGAIN)
    assert demoted.state is VocabularyState.USED
    assert demoted.srs.lapses == 1 and demoted.srs.interval_days == 0


def test_reviews_are_idempotent_per_event_and_identified_words_become_reviewed(
    container: Container,
) -> None:
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    entry = container.lexicon.add_manual(learner.id, "die Übelkeit", "la nausée", "")
    assert entry.state is VocabularyState.IDENTIFIED

    first = container.lexicon.review(learner.id, entry.id, "event-1", SrsRating.GOOD)
    replay = container.lexicon.review(learner.id, entry.id, "event-1", SrsRating.EASY)

    assert first.state is VocabularyState.REVIEWED
    assert replay.srs.repetitions == first.srs.repetitions == 1
    assert replay.srs.interval_days == 1
    # Adding the same word again updates instead of duplicating, and unarchives it.
    container.lexicon.set_archived(learner.id, entry.id, True)
    again = container.lexicon.add_manual(learner.id, "Die Übelkeit", "", "Mir ist übel.")
    assert again.id == entry.id and again.archived is False
    assert again.translation == "la nausée" and again.example == "Mir ist übel."


def test_lexicon_api_is_owned_by_the_profile(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as alice, TestClient(app) as bob:
        alice.post("/api/learners", json={"target_cefr": "C1"})
        bob.post("/api/learners", json={"target_cefr": "B2"})
        empty = alice.get("/api/lexicon").json()
        assert empty["due_count"] == 0 and empty["entries"] == []
        assert empty["srs_version"] == "srs-sm2-v1"

        created = alice.post(
            "/api/lexicon/entries", json={"lemma": "der Husten", "translation": "la toux"}
        )
        assert created.status_code == 201
        entry_id = created.json()["id"]
        assert created.json()["due"] is True

        reviewed = alice.post(
            f"/api/lexicon/entries/{entry_id}/reviews",
            json={"event_id": "evt-1", "rating": "good"},
        ).json()
        assert reviewed["state"] == "reviewed" and reviewed["due"] is False
        overview = alice.get("/api/lexicon").json()
        assert overview["by_state"]["reviewed"] == 1 and overview["due_count"] == 0
        assert overview["active_count"] == 1 and overview["acquired_count"] == 0
        assert overview["maintenance_cadence_days"] == 30
        assert overview["entries"][0]["zone"] == "active"
        me = alice.get("/api/profile").json()
        alice.patch(f"/api/learners/{me['id']}/profile", json={"maintenance_cadence_days": 7})
        assert alice.get("/api/lexicon").json()["maintenance_cadence_days"] == 7
        assert (
            alice.patch(
                f"/api/learners/{me['id']}/profile", json={"maintenance_cadence_days": 10}
            ).status_code
            == 422
        )

        assert bob.get("/api/lexicon").json()["entries"] == []
        assert (
            bob.patch(f"/api/lexicon/entries/{entry_id}", json={"archived": True}).status_code
            == 404
        )
        assert (
            bob.post(
                f"/api/lexicon/entries/{entry_id}/reviews",
                json={"event_id": "evt-2", "rating": "good"},
            ).status_code
            == 404
        )
        archived = alice.patch(f"/api/lexicon/entries/{entry_id}", json={"archived": True}).json()
        assert archived["archived"] is True
        assert alice.get("/api/lexicon").json()["entries"] == []
    with pytest.raises(NotFoundError):
        container.lexicon.repository.get("someone-else", entry_id)


def test_completed_session_view_carries_the_lexicon_report(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        case = client.get("/api/cases").json()[0]
        learner = client.post("/api/learners", json={"target_cefr": "C1"}).json()
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
            for text in ("Guten Tag!", "Was ist mit fact-1?", FRENCH_QUESTION):
                socket.send_json({"type": "debug.transcript", "transcript": text})
                while socket.receive_json()["type"] != "turn.completed":
                    pass
            socket.send_json({"type": "call.end"})
            assert socket.receive_json()["type"] == "call.ended"
        active = client.get(f"/api/sessions/{session['id']}").json()
        assert active["lexicon"] is None
        assert active["turns"][2]["patient_response_kind"] == "wrong_language"

        completed = client.post(f"/api/sessions/{session['id']}/end", json={}).json()
        assert completed["evaluation"]["schema_version"] == "session-evaluation-v4"
        assert completed["evaluation"]["code_switches"][0]["turn"] == 3
        assert completed["vocabulary"][0]["kind"] == "missing"
        assert {e["lemma"] for e in completed["lexicon"]["added"]} == {"ausstrahlen", "Schmerz"}
        assert completed["lexicon"]["wrong_language_turns"] == [3]
        assert client.get("/api/lexicon").json()["due_count"] == 2
