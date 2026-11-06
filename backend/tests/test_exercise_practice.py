"""Serving the bank: the working level per category, the draw, the report, the programme slot."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from accounts_fixtures import sign_in
from conftest import build_test_container
from fastapi.testclient import TestClient
from test_language_exercises_domain import CLOZE, CORRECTION, candidate
from test_program import model

from ari.api.app import create_app
from ari.application.services.exercise_practice import REPORTS_PER_DAY, ExercisePracticeService
from ari.application.services.program import plan_week
from ari.container import Container
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.exercise_practice import (
    LEVEL_WINDOW,
    RETRY_AFTER,
    SERIES_ITEMS,
    drawable,
    focus_candidates,
    series_completions,
    start_level,
    working_level,
)
from ari.domain.language_drill import LanguageProfile
from ari.domain.language_exercises import ExerciseReview, LanguageDrill, LanguageExercise
from ari.domain.models import CEFRLevel, new_id
from ari.domain.placement import PlacementAttempt
from ari.infrastructure.persistence.platform.exercises import SqlDrillStore, SqlExerciseBank

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
REVIEWS = [
    ExerciseReview("rubric_judge", "anthropic:claude-haiku-5-5@exercise-rubric-v3", "approve", {})
]
NAMES = [
    "Müller",
    "Schmidt",
    "Weber",
    "Wagner",
    "Becker",
    "Hoffmann",
    "Koch",
    "Richter",
    "Klein",
    "Wolf",
    "Braun",
    "Zimmer",
]


def gender(n: int, level: str = "B1") -> LanguageExercise:
    stem = f"Haben Sie ___ Tablette heute schon genommen, Frau {NAMES[n]}?"
    return LanguageExercise.model_validate_json(
        candidate(CLOZE, stem=stem, level=level).model_dump_json()
    )


def answer(
    exercise: LanguageExercise, correct: bool, at: datetime = NOW, level: str | None = None
) -> LanguageDrill:
    return LanguageDrill(
        id=new_id(),
        learner_id="l1",
        request_id=new_id(),
        exercise_id=exercise.exercise_id,
        exercise_hash=exercise.content_hash,
        level=level or exercise.level,
        category=exercise.category,
        chosen_index=exercise.answer_index if correct else (exercise.answer_index + 1) % 3,
        correct=correct,
        answered_at=at,
    )


def series(correct: list[bool], level: str = "B1", start: datetime = NOW) -> list[LanguageDrill]:
    item = gender(0, level)
    return [answer(item, ok, start + timedelta(minutes=i)) for i, ok in enumerate(correct)]


# ----- working-level-v1 -------------------------------------------------------------------


def test_the_start_level_is_the_learners_level_held_within_the_bank() -> None:
    assert [start_level(x) for x in (None, "A1", "A2", "B1", "B2", "C1", "C2")] == [
        "B1",
        "B1",
        "B1",
        "B1",
        "B2",
        "C1",
        "C1",
    ]


def test_ten_answers_at_eighty_percent_move_up_one_level_and_the_count_restarts() -> None:
    eight_of_ten = [True] * 8 + [False] * 2
    assert working_level("B1", series(eight_of_ten[:-1])) == "B1"  # Nine answers: not enough.
    assert working_level("B1", series(eight_of_ten)) == "B2"
    # Answers given at B1 do not count at B2: ten more are needed there.
    assert working_level("B1", series(eight_of_ten + [True] * 9)) == "B2"
    later = series([True] * LEVEL_WINDOW, "B2", NOW + timedelta(hours=1))
    assert working_level("B1", [*series(eight_of_ten), *later]) == "C1"
    assert working_level("C1", series([True] * 20, "C1")) == "C1"  # Capped.


def test_under_half_right_moves_down_and_b1_is_the_floor() -> None:
    assert working_level("B2", series([True] * 5 + [False] * 5, "B2")) == "B2"  # 50 % stays.
    assert working_level("B2", series([True] * 4 + [False] * 6, "B2")) == "B1"
    assert working_level("B1", series([False] * 20)) == "B1"


def test_the_last_ten_answers_decide_and_a_new_placement_restarts_the_count() -> None:
    answers = series([False] * 6 + [True] * 10)  # The window slides: the last ten are right.
    assert working_level("B1", answers) == "B2"
    assert working_level("B1", answers, since=answers[-1].answered_at) == "B1"


# ----- exercise-draw-v1 -------------------------------------------------------------------


def test_fresh_exercises_come_in_an_order_of_the_learners_own() -> None:
    bank = [gender(n) for n in range(8)]
    first = [e.exercise_id for e in drawable(bank, [], "learner-a", NOW)]
    assert sorted(first) == sorted(e.exercise_id for e in bank)
    assert first == [e.exercise_id for e in drawable(list(reversed(bank)), [], "learner-a", NOW)]
    assert first != [e.exercise_id for e in drawable(bank, [], "learner-b", NOW)]


def test_an_exercise_answered_right_never_comes_back_a_missed_one_does_later() -> None:
    right, missed, fresh = gender(0), gender(1), gender(2)
    history = [answer(right, True), answer(missed, False)]
    soon = drawable([right, missed, fresh], history, "l1", NOW + timedelta(hours=1))
    assert soon == [fresh]
    later = drawable([right, missed, fresh], history, "l1", NOW + RETRY_AFTER)
    assert later == [fresh, missed]
    # Once missed then right, it is done.
    done = [*history, answer(missed, True, NOW + timedelta(days=2))]
    assert drawable([right, missed], done, "l1", NOW + timedelta(days=3)) == []


def test_missed_exercises_come_back_the_oldest_miss_first() -> None:
    a, b = gender(0), gender(1)
    history = [answer(b, False, NOW), answer(a, False, NOW + timedelta(hours=2))]
    assert drawable([a, b], history, "l1", NOW + timedelta(days=2)) == [b, a]


def test_a_held_exercise_is_not_drawn() -> None:
    a, b = gender(0), gender(1)
    assert drawable([a, b], [], "l1", NOW, held=frozenset({a.exercise_id})) == [b]


def test_the_focus_is_recurring_errors_first_then_placement_weak_points_without_repeats() -> None:
    profile = LanguageProfile(
        sessions_considered=3,
        errors_by_category={"case": 4, "gender": 1, "other": 3},
        recurring=("case",),
        focus="case",
        focus_errors=4,
        drill_items=2,
    )
    weak = [
        {"category": "case", "written_wrong": 2, "spoken_errors": 0},
        {"category": "verb_form", "written_wrong": 1, "spoken_errors": 2},
        {"category": "unknown"},
    ]
    found = [(c.category, c.source, c.errors) for c in focus_candidates(profile, weak)]
    assert found == [("case", "sessions", 4), ("verb_form", "placement", 3)]
    empty = LanguageProfile(0, {}, (), None, 0, 0)
    assert focus_candidates(empty, []) == []


def test_every_fifth_answer_completes_a_series() -> None:
    answers = series([True] * 12, start=NOW)
    done = series_completions(answers, NOW)
    assert done == [answers[4].answered_at, answers[9].answered_at]
    assert series_completions(answers, NOW + timedelta(minutes=3)) == [answers[7].answered_at]


# ----- the service on the database --------------------------------------------------------


class Attempts:
    def __init__(self, attempt: PlacementAttempt | None = None) -> None:
        self.attempt = attempt

    def get(self, attempt_id: str, learner_id: str) -> PlacementAttempt:
        if self.attempt is None or self.attempt.id != attempt_id:
            raise NotFoundError("attempt")
        return self.attempt


@pytest.fixture
def container(database_url: str) -> Any:
    services = build_test_container(database_url)
    yield services
    services.repository.engine.dispose()


def learner_at(container: Container, level: str | None, **details: Any) -> str:
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    changed = replace(
        learner.details,
        estimated_level=CEFRLevel(level) if level else None,
        estimated_at=NOW - timedelta(days=1) if level else None,
        **details,
    )
    container.repository.update_learner(replace(learner, details=changed))
    return learner.id


def service(container: Container, attempts: Attempts | None = None) -> ExercisePracticeService:
    engine = container.repository.engine
    return ExercisePracticeService(
        SqlExerciseBank(engine),
        SqlDrillStore(engine),
        container.repository,
        attempts or Attempts(),
        clock=lambda: NOW,
    )


def stock(practice: ExercisePracticeService, items: list[LanguageExercise]) -> None:
    for item in items:
        assert practice.bank.add(item, batch_id="b1", provenance={}, reviews=REVIEWS)


def test_the_next_exercise_is_drawn_at_the_working_level_without_its_key(
    container: Container,
) -> None:
    practice = service(container)
    stock(practice, [gender(0), gender(1), gender(2, "B2")])
    learner = learner_at(container, "B2")
    drawn = practice.next(learner, "gender")
    assert (drawn["level"], drawn["remaining"]) == ("B2", 1)
    exercise = drawn["exercise"]
    assert exercise["id"] == gender(2, "B2").exercise_id
    assert "answer_index" not in exercise and "explanation_fr" not in exercise
    assert practice.next(learner, "case")["exercise"] is None
    with pytest.raises(InvalidStateError):
        practice.next(learner, "spelling")


def test_an_answer_is_graded_by_the_code_and_kept_once_per_request(container: Container) -> None:
    practice = service(container)
    item = gender(0)
    stock(practice, [item, gender(1)])
    learner = learner_at(container, "B1")
    wrong = (item.answer_index + 1) % len(item.options)
    result = practice.answer(
        learner, request_id="r1", exercise_id=item.exercise_id, chosen_index=wrong
    )
    assert result["correct"] is False and result["answer_index"] == item.answer_index
    assert result["correct_sentence"] == item.correct_sentence()
    assert result["explanation_fr"] == item.explanation_fr
    # The same request again is the same answer, even with another choice.
    again = practice.answer(
        learner, request_id="r1", exercise_id=item.exercise_id, chosen_index=item.answer_index
    )
    assert again["correct"] is False and len(practice.answers.of_learner(learner)) == 1
    with pytest.raises(InvalidStateError, match="autre exercice"):
        practice.answer(learner, request_id="r1", exercise_id=gender(1).exercise_id, chosen_index=0)
    with pytest.raises(InvalidStateError, match="hors des options"):
        practice.answer(learner, request_id="r2", exercise_id=item.exercise_id, chosen_index=7)
    assert practice.next(learner, "gender")["exercise"]["id"] == gender(1).exercise_id


def test_a_withdrawn_exercise_cannot_be_answered(container: Container) -> None:
    practice = service(container)
    item = gender(0)
    stock(practice, [item])
    practice.bank.withdraw(item.exercise_id, reason="faux", actor="Ismail")
    learner = learner_at(container, "B1")
    with pytest.raises(InvalidStateError, match="retiré"):
        practice.answer(learner, request_id="r1", exercise_id=item.exercise_id, chosen_index=0)


def test_a_report_holds_the_exercise_back_from_everyone_until_a_person_reviews_it(
    container: Container,
) -> None:
    practice = service(container)
    item = gender(0)
    stock(practice, [item, gender(1)])
    reporter, other = learner_at(container, "B1"), learner_at(container, "B1")
    assert practice.next(other, "gender")["remaining"] == 2
    first = practice.report(reporter, item.exercise_id, reason="wrong_key", comment="  die ?  ")
    assert first == {"reported": True, "already_reported": False}
    assert practice.report(reporter, item.exercise_id, reason="other", comment=None)[
        "already_reported"
    ]
    assert practice.bank.held_for_review() == frozenset({item.exercise_id})
    assert practice.next(other, "gender")["remaining"] == 1
    reports = [
        r for r in practice.bank.reviews(item.exercise_id) if r.reviewer_kind == "learner_report"
    ]
    assert len(reports) == 1 and reports[0].payload["comment"] == "die ?"
    # A person puts it back: drawn again, and a new report holds it again.
    practice.bank.add_review(item.exercise_id, ExerciseReview("human", "Ismail", "approve", {}))
    assert practice.bank.held_for_review() == frozenset()
    assert practice.next(other, "gender")["remaining"] == 2
    assert not practice.report(reporter, item.exercise_id, reason="unclear", comment=None)[
        "already_reported"
    ]
    assert practice.bank.held_for_review() == frozenset({item.exercise_id})
    # Withdrawn: no longer held, simply gone.
    practice.bank.withdraw(item.exercise_id, reason="faux", actor="Ismail")
    assert practice.bank.held_for_review() == frozenset()
    with pytest.raises(NotFoundError):
        practice.report(reporter, "lx-unknown", reason="other", comment=None)


def test_the_focus_comes_from_the_placement_weak_points_and_needs_exercises_left(
    container: Container,
) -> None:
    attempt = PlacementAttempt(
        id="a1",
        learner_id="x",
        request_id="r",
        set_id="ari-placement-de",
        set_version="2",
        set_hash="0" * 64,
        status="completed",
        phase="completed",
        state={},
        result={
            "weak_points": [
                {"category": "case", "written_wrong": 3, "spoken_errors": 0},
                {"category": "gender", "written_wrong": 2, "spoken_errors": 1},
            ]
        },
    )
    practice = service(container, Attempts(attempt))
    stock(practice, [gender(0), gender(1)])
    learner = learner_at(container, "B1", placement_attempt_id="a1")
    focus = practice.focus(learner)
    assert focus is not None
    # No case exercise in the bank: the next weak point with exercises is the focus.
    assert (focus["category"], focus["source"], focus["errors"]) == ("gender", "placement", 3)
    assert (focus["level"], focus["available"], focus["items"]) == ("B1", 2, 2)
    assert focus["rationale"] == (
        "Exercices sur le genre, niveau B1 : un point faible de votre test de niveau."
    )
    assert practice.focus(learner_at(container, "B1")) is None  # Nothing established.
    overview = practice.overview(learner)
    by_category = {c["category"]: c for c in overview["categories"]}
    assert by_category["gender"]["available"] == 2 and by_category["case"]["available"] == 0
    assert overview["focus"]["category"] == "gender"


# ----- the programme ----------------------------------------------------------------------


FOCUS = {
    "category": "gender",
    "label": "le genre",
    "level": "B2",
    "source": "placement",
    "errors": 3,
    "available": 12,
    "items": 5,
    "rationale": "Exercices sur le genre, niveau B2 : un point faible de votre test de niveau.",
}


def test_the_programme_plans_two_series_on_the_focus_and_counts_them_done() -> None:
    today = date(2026, 11, 5)  # A Thursday: the Monday series is past.
    week = plan_week(
        model(estimated_level="B2", started_on=date(2026, 10, 1)),
        (),
        (),
        today,
        completed={},
        exercise_focus=FOCUS,
    )
    slots = [s for s in week["slots"] if s["kind"] == "language_exercises"]
    assert [s["day_offset"] for s in slots] == [0, 3]
    assert slots[0]["minutes"] == 6 and slots[0]["rationale"] == FOCUS["rationale"]
    assert slots[0]["recommended"] == {
        "kind": "language_exercises",
        "category": "gender",
        "label": "le genre",
        "level": "B2",
        "items": 5,
    }
    assert [s["state"] for s in slots] == ["missed", "today"]
    done = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    week = plan_week(
        model(estimated_level="B2", started_on=date(2026, 10, 1)),
        (),
        (),
        today,
        completed={"language_exercises": [done]},
        exercise_focus=FOCUS,
    )
    states = [s["state"] for s in week["slots"] if s["kind"] == "language_exercises"]
    assert states == ["done", "today"]


def test_no_series_is_planned_without_a_focus_or_before_the_placement_test() -> None:
    today = date(2026, 11, 5)
    assert not [
        s
        for s in plan_week(
            model(estimated_level="B2", started_on=date(2026, 10, 1)), (), (), today, completed={}
        )["slots"]
        if s["kind"] == "language_exercises"
    ]
    placement_first = plan_week(
        model(estimated_level=None, declared_level=None),
        (),
        (),
        today,
        completed={},
        exercise_focus=FOCUS,
    )
    assert not [s for s in placement_first["slots"] if s["kind"] == "language_exercises"]


# ----- the learner's API ------------------------------------------------------------------


def test_the_learner_draws_answers_and_reports_through_the_api(database_url: str) -> None:
    container = build_test_container(database_url)
    bank = SqlExerciseBank(container.repository.engine)
    items = [gender(n) for n in range(SERIES_ITEMS + 1)]
    correction = LanguageExercise.model_validate_json(candidate(CORRECTION).model_dump_json())
    for item in [*items, correction]:
        bank.add(item, batch_id="b1", provenance={}, reviews=REVIEWS)
    app = create_app(container)
    with TestClient(app) as client:
        assert client.get("/api/language-exercises").status_code == 401
        # The remediation drill sits behind the same boundary (it was left out of it).
        assert client.get("/api/language-drill").status_code == 401
        sign_in(client)
        client.post("/api/learners", json={"target_cefr": "C1"})
        overview = client.get("/api/language-exercises").json()
        assert overview["series_items"] == SERIES_ITEMS and overview["focus"] is None
        by_category = {c["category"]: c for c in overview["categories"]}
        assert by_category["gender"]["available"] == SERIES_ITEMS + 1
        assert client.get("/api/language-exercises/next?category=spelling").status_code == 422

        drawn = client.get("/api/language-exercises/next?category=gender").json()["exercise"]
        item = next(i for i in items if i.exercise_id == drawn["id"])
        answered = client.post(
            "/api/language-exercises/answers",
            json={
                "request_id": "r1",
                "exercise_id": drawn["id"],
                "chosen_index": item.answer_index,
            },
        )
        assert answered.status_code == 200 and answered.json()["correct"] is True
        bad = client.post(
            "/api/language-exercises/answers",
            json={"request_id": "r2", "exercise_id": drawn["id"], "chosen_index": "0"},
        )
        assert bad.status_code == 422

        following = client.get("/api/language-exercises/next?category=gender").json()
        assert following["remaining"] == SERIES_ITEMS and following["exercise"]["id"] != drawn["id"]
        report = client.post(
            f"/api/language-exercises/{following['exercise']['id']}/report",
            json={"reason": "several_answers", "comment": "der et die ?"},
        )
        assert report.status_code == 200 and report.json()["reported"] is True
        assert (
            client.post(
                f"/api/language-exercises/{following['exercise']['id']}/report",
                json={"reason": "x"},
            ).status_code
            == 422
        )
        after = client.get("/api/language-exercises/next?category=gender").json()
        assert after["remaining"] == SERIES_ITEMS - 1

        program = client.get("/api/program").json()
        assert program["week"]["version"] == "program-rules-v5"
    container.repository.engine.dispose()


def test_one_learner_cannot_hold_back_more_than_ten_exercises_a_day(container: Container) -> None:
    practice = service(container)
    items = [gender(n) for n in range(REPORTS_PER_DAY + 1)]
    stock(practice, items)
    learner = learner_at(container, "B1")
    for item in items[:REPORTS_PER_DAY]:
        practice.report(learner, item.exercise_id, reason="other", comment=None)
    with pytest.raises(InvalidStateError, match="Réessayez demain"):
        practice.report(learner, items[REPORTS_PER_DAY].exercise_id, reason="other", comment=None)
    # Another learner, or the same one the next day, may report again.
    practice.report(
        learner_at(container, "B1"), items[-1].exercise_id, reason="other", comment=None
    )
    tomorrow = ExercisePracticeService(
        practice.bank,
        practice.answers,
        container.repository,
        Attempts(),
        clock=lambda: NOW + timedelta(days=1, minutes=1),
    )
    tomorrow.report(learner, items[REPORTS_PER_DAY].exercise_id, reason="other", comment=None)


def test_simultaneous_reports_from_one_learner_never_pass_the_cap(container: Container) -> None:
    practice = service(container)
    items = [gender(n) for n in range(len(NAMES))]
    stock(practice, items)
    learner = learner_at(container, "B1")
    start = threading.Barrier(len(items) + 2)

    def report(exercise_id: str) -> str:
        start.wait()
        try:
            practice.report(learner, exercise_id, reason="other", comment=None)
        except InvalidStateError:
            return "capped"
        return "kept"

    # Every exercise at once, and the first one twice: the same learner's second report on it
    # must find the first one open, not add another.
    targets = [item.exercise_id for item in items] + [items[0].exercise_id] * 2
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        outcomes = list(pool.map(report, targets))
    name = f"learner:{learner}"

    def mine(exercise_id: str) -> int:
        return sum(r.reviewer_name == name for r in practice.bank.reviews(exercise_id))

    assert sum(mine(item.exercise_id) for item in items) == REPORTS_PER_DAY
    first = mine(items[0].exercise_id)
    assert first <= 1
    # The two repeats are thanked as already reported when the first report was kept.
    assert outcomes.count("kept") == REPORTS_PER_DAY + 2 * first
