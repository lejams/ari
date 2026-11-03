"""The remediation drill "Votre phrase": the learner's own errors, replayed and checked by code."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from accounts_fixtures import sign_in
from clinical_fixtures import synthetic_bundle
from conftest import build_test_container, publish_with_simulated_reviews
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.container import Container
from ari.domain.language_drill import (
    DRILL_ITEMS,
    DrillAnswer,
    DrillItem,
    LanguageDrill,
    check_answer,
    drill_candidates,
    item_id,
    language_profile,
    select_items,
)
from ari.domain.models import (
    CEFRLevel,
    ConversationSession,
    Evaluation,
    EvidenceObservation,
    LearningGoal,
    LearningMode,
    SessionStatus,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def err(
    category: str | None, quote: str | None = None, correction: str | None = None
) -> EvidenceObservation:
    text = f"« {quote} » → « {correction} » : explication"
    return EvidenceObservation(text, (1,), category, quote, correction)


def session(
    index: int,
    errors: tuple[EvidenceObservation, ...],
    *,
    status: SessionStatus = SessionStatus.COMPLETED,
    evaluation_account: bool = False,
) -> ConversationSession:
    created = NOW - timedelta(days=20 - index)  # Higher index = more recent.
    evaluation = Evaluation(
        schema_version="session-evaluation-v5",
        prompt_version="t",
        rubric_version="r",
        overall_score=0,
        max_score=10,
        summary="",
        strengths=(),
        priorities=(),
        missed_fact_ids=(),
        language_errors=errors,
        criteria=(),
    )
    return ConversationSession(
        id=f"s{index}",
        learner_id="l",
        case_id="c",
        case_version="1",
        case_hash="h",
        goal=LearningGoal(),
        status=status,
        evaluation=evaluation,
        learning_mode=LearningMode.TRAINING,
        created_at=created,
        call_started_at=created,
        ended_at=created + timedelta(minutes=12),
        evaluation_account=evaluation_account,
    )


CASE_1 = err("case", "mit der Arzt", "mit dem Arzt")
CASE_2 = err("case", "für die Schmerz", "für den Schmerz")
GENDER = err("gender", "das Tablette", "die Tablette")
VERB = err("verb_form", "ich habe gegangen", "ich bin gegangen")
LEGACY = err("case")  # Before session-evaluation-v5: no quote, no correction.


def test_answers_are_compared_with_the_correction_ignoring_case_spaces_and_final_dot() -> None:
    item = drill_candidates((session(1, (CASE_1,)),))[0]
    assert check_answer(item, "  Mit dem   arzt. ") == "matched"
    assert check_answer(item, "mit dem Arzt") == "matched"
    assert check_answer(item, "mit den Arzt") == "not_matched"  # never a failure, just not matched
    assert check_answer(item, "mit der Arzt") == "not_matched"  # the mistake itself


def test_candidates_are_the_replayable_errors_newest_first_without_duplicates() -> None:
    sessions = (
        session(1, (CASE_1, GENDER)),
        session(2, (CASE_2, CASE_2, LEGACY, err("other", "xx", "yy"), err(None, "a", "b"))),
        session(3, (VERB,)),
    )
    found = drill_candidates(sessions)
    # Newest session first; the duplicate, the legacy error, "other" and the uncategorised go.
    assert [i.quote for i in found] == [
        "ich habe gegangen",
        "für die Schmerz",
        "mit der Arzt",
        "das Tablette",
    ]
    assert found[0].session_id == "s3" and found[0].id == item_id(
        "s3", VERB.quote or "", VERB.correction or ""
    )
    assert found[0].note == "explication"  # the quote and its correction are not printed twice
    other = EvidenceObservation("Texte libre", (1,), "case", "mit der Arzt", "mit dem Arzt")
    assert drill_candidates((session(1, (other,)),))[0].note == "Texte libre"
    # The same mistake in two sessions is two items: that it came back is the point.
    twice = drill_candidates((session(1, (CASE_1,)), session(2, (CASE_1,))))
    assert [i.session_id for i in twice] == ["s2", "s1"]


def test_only_completed_non_evaluation_sessions_of_the_window_count() -> None:
    sessions = [session(i, (CASE_1,)) for i in range(1, 7)]
    assert len(drill_candidates(sessions)) == 5  # the five most recent sessions
    mixed = (
        session(1, (CASE_1,), status=SessionStatus.ACTIVE),
        session(2, (CASE_2,), evaluation_account=True),
        session(3, (GENDER,)),
    )
    assert [i.category for i in drill_candidates(mixed)] == ["gender"]


def test_focus_needs_a_recurring_category_with_a_replayable_item() -> None:
    assert language_profile(()).focus is None
    one = language_profile((session(1, (CASE_1,)),))
    assert one.focus is None and one.errors_by_category == {"case": 1}
    twice = language_profile((session(1, (CASE_1, CASE_2)),))
    assert (twice.focus, twice.focus_errors, twice.drill_items) == ("case", 2, 2)
    assert twice.recurring == ("case",) and twice.sessions_considered == 1
    # Two errors of a category but only legacy ones: counted, never drilled.
    legacy = language_profile((session(1, (LEGACY, LEGACY)),))
    assert legacy.errors_by_category == {"case": 2} and legacy.focus is None
    assert legacy.recurring == ("case",)
    # "other" never sets the focus.
    other = language_profile(
        (session(1, (err("other", "a b", "a c"), err("other", "d e", "d f"))),)
    )
    assert other.focus is None and other.recurring == ()


def test_focus_is_the_most_frequent_category_then_the_most_recent() -> None:
    frequent = language_profile(
        (session(1, (CASE_1, CASE_2, err("case", "dem Frau", "der Frau"), GENDER)),)
    )
    assert frequent.focus == "case" and frequent.errors_by_category == {"case": 3, "gender": 1}
    sessions = (
        session(1, (CASE_1, CASE_2, GENDER)),
        session(2, (err("gender", "das Kopf", "der Kopf"),)),
    )
    # Two case errors and two gender errors: the gender error is more recent.
    assert language_profile(sessions).focus == "gender"
    more_case = (*sessions, session(3, (err("case", "mit die Frau", "mit der Frau"),)))
    assert language_profile(more_case).focus == "case"


def test_matched_items_are_not_asked_again_and_move_the_focus() -> None:
    sessions = (session(1, (CASE_1, CASE_2, GENDER, err("gender", "das Kopf", "der Kopf"))),)
    items = select_items(sessions, "case")
    assert len(items) == 2
    matched = frozenset({items[0].id})
    assert [i.id for i in select_items(sessions, "case", matched)] == [items[1].id]
    # Once every case item is matched, the case category cannot be the focus any more.
    both = frozenset(i.id for i in items)
    assert language_profile(sessions, both).focus == "gender"


def test_a_drill_holds_at_most_five_items_of_one_category() -> None:
    errors = tuple(
        err("verb_form", f"ich gehe{n} gestern", f"ich ging{n} gestern") for n in range(8)
    )
    profile = language_profile((session(1, errors),))
    assert profile.drill_items == DRILL_ITEMS
    items: tuple[DrillItem, ...] = select_items((session(1, errors),), "verb_form")
    assert len(items) == DRILL_ITEMS


# ----- service and API, on the fake providers ----------------------------------------------------


async def _session_with_errors(container: Container, learner_id: str) -> None:
    """A real analysed session: the fake evaluator reports an error per "Wo haben…" question."""
    case = container.cases.list()[0]
    created = container.orchestrator.create_session(learner_id, case.id, case.version)
    container.orchestrator.activate(created.id)
    for question in ("Wo haben Sie Schmerzen?", "Wo haben Sie Fieber?", "Seit wann?"):
        await container.orchestrator.process_transcript(created.id, question)
    await container.orchestrator.end_session(created.id)


@pytest.mark.asyncio
async def test_the_drill_replays_the_learners_errors_end_to_end(database_url: str) -> None:
    container = build_test_container(database_url)
    publish_with_simulated_reviews(container.cases.store, synthetic_bundle())
    app = create_app(container)
    with TestClient(app) as client:
        sign_in(client)
        client.post("/api/learners", json={"target_cefr": "C1"})
        learner_id = client.get("/api/profile").json()["id"]

        none = client.get("/api/language-drill").json()
        assert none["available"] is False and none["focus_category"] is None
        refused = client.post("/api/language-drill/drills", json={"request_id": "r0"})
        assert refused.status_code == 400 and "Aucune erreur récurrente" in refused.text

        await _session_with_errors(container, learner_id)
        # The structured fields survive the analysis, the database and the reload.
        evaluation = container.repository.list_sessions(learner_id)[0].evaluation
        assert evaluation is not None and evaluation.schema_version == "session-evaluation-v5"
        assert [e.quote for e in evaluation.language_errors] == [
            "Wo haben Sie Schmerzen?",
            "Wo haben Sie Fieber?",
        ]
        assert evaluation.language_errors[0].correction == "Wo haben Sie Schmerzen? (korrigiert)"

        overview = client.get("/api/language-drill").json()
        assert overview["available"] is True and overview["focus_category"] == "case"
        assert (overview["focus_errors"], overview["items"]) == (2, 2)

        started = client.post("/api/language-drill/drills", json={"request_id": "r1"})
        assert started.status_code == 201
        drill = started.json()
        drill_id = drill["id"]
        assert drill["progress"] == {"answered": 0, "total": 2} and drill["result"] is None
        # Nothing of the correction is shown before the learner tried.
        assert all("correction" not in item and "note" not in item for item in drill["items"])
        # Same request, or another request while this one is open: the same drill.
        assert (
            client.post("/api/language-drill/drills", json={"request_id": "r1"}).json()["id"]
            == drill_id
        )
        assert (
            client.post("/api/language-drill/drills", json={"request_id": "r2"}).json()["id"]
            == drill_id
        )

        first, second = drill["items"]
        url = f"/api/language-drill/drills/{drill_id}"
        # Items are answered in order.
        wrong_order = client.post(
            f"{url}/answers", json={"event_id": "e0", "item_id": second["id"], "text": "x"}
        )
        assert wrong_order.status_code == 400
        answered = client.post(
            f"{url}/answers",
            json={
                "event_id": "e1",
                "item_id": first["id"],
                "text": "wo haben sie fieber? (korrigiert)",
            },
        ).json()
        assert answered["items"][0]["outcome"] == "not_matched"  # an answer to the other phrase
        assert answered["items"][0]["correction"].endswith("(korrigiert)")
        assert answered["items"][0]["note"].startswith("Fake : vérifier l'accord")
        assert answered["progress"]["answered"] == 1 and answered["current_item_id"] == second["id"]
        # A replayed event changes nothing; the same event with another text is refused.
        again = client.post(
            f"{url}/answers",
            json={
                "event_id": "e1",
                "item_id": first["id"],
                "text": "wo haben sie fieber? (korrigiert)",
            },
        )
        assert again.status_code == 200 and again.json()["progress"]["answered"] == 1
        clash = client.post(
            f"{url}/answers", json={"event_id": "e1", "item_id": first["id"], "text": "autre"}
        )
        assert clash.status_code == 400
        matched = client.post(
            f"{url}/answers",
            json={
                "event_id": "e2",
                "item_id": second["id"],
                "text": " Wo haben Sie Fieber? (KORRIGIERT) ",
            },
        ).json()
        assert matched["items"][1]["outcome"] == "matched"
        finished = client.post(f"{url}/finish").json()
        assert finished["status"] == "completed"
        assert finished["result"] == {"matched": 1, "answered": 2, "total": 2}
        assert client.post(f"{url}/finish").json()["ended_at"] == finished["ended_at"]
        late = client.post(
            f"{url}/answers", json={"event_id": "e3", "item_id": second["id"], "text": "x"}
        )
        assert late.status_code == 400

        # The matched phrase is not asked again; the missed one is the only one left, so the
        # category still has a replayable item, and a new request opens a new drill.
        after = client.get("/api/language-drill").json()
        assert after["available"] is True and after["items"] == 1
        new = client.post("/api/language-drill/drills", json={"request_id": "r3"}).json()
        assert new["id"] != drill_id and new["progress"]["total"] == 1
        # Another learner cannot reach it.
        with TestClient(app) as stranger:
            sign_in(stranger)
            assert stranger.get(f"/api/language-drill/drills/{new['id']}").status_code == 404

        # The programme announces the drill, then marks it done once one is completed.
        program = client.get("/api/program").json()
        assert program["model"]["version"] == "learner-model-v2"
        assert program["model"]["language"]["focus"] == "case"
        assert program["week"]["version"] == "program-rules-v4"


def test_a_session_without_any_structured_error_gives_no_drill(database_url: str) -> None:
    container = build_test_container(database_url)
    publish_with_simulated_reviews(container.cases.store, synthetic_bundle())
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    assert container.language_drills.overview(learner.id)["available"] is False


def test_two_tabs_cannot_start_two_unfinished_drills(database_url: str) -> None:
    container = build_test_container(database_url)
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    repository = container.language_drills.repository
    item = DrillItem(
        id="i1", session_id="s1", category="case", quote="q", correction="c", note="", turns=(1,)
    )

    def drill(identifier: str, request_id: str) -> LanguageDrill:
        return LanguageDrill(
            id=identifier,
            learner_id=learner.id,
            request_id=request_id,
            focus_category="case",
            items=(item,),
        )

    first = repository.create(drill("d1", "tab-1"))
    # Another tab read an empty list before the first insert: it gets the winner, not a second row.
    second = repository.create(drill("d2", "tab-2"))
    assert second.id == first.id == "d1"
    assert [d.id for d in repository.list(learner.id)] == ["d1"]

    # A finished drill frees the slot for the next one.
    repository.append_answer(
        "d1", learner.id, DrillAnswer("e1", "i1", "c", "matched", first.created_at)
    )
    repository.finish("d1", learner.id)
    third = repository.create(drill("d3", "tab-3"))
    assert third.id == "d3"
    assert sorted(d.id for d in repository.list(learner.id)) == ["d1", "d3"]
