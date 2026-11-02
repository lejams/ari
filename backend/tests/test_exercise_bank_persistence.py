"""The bank: content never changes, a withdrawal is final, checks and answers are append-only."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from conftest import build_test_container
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_language_exercises_domain import CLOZE, CORRECTION, candidate

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.language_exercises import ExerciseReview, LanguageDrill, LanguageExercise, grade
from ari.domain.models import CEFRLevel, new_id
from ari.infrastructure.persistence.platform.exercises import SqlDrillStore, SqlExerciseBank

REVIEWS = [
    ExerciseReview("blind_solver", "anthropic:claude-sonnet-5-5@exercise-solver-v1", "approve", {}),
    ExerciseReview("rubric_judge", "anthropic:claude-sonnet-5-5@exercise-rubric-v1", "approve", {}),
]


def exercise(base: dict[str, Any] = CLOZE, **changes: Any) -> LanguageExercise:
    return LanguageExercise.model_validate_json(candidate(base, **changes).model_dump_json())


@pytest.fixture
def engine(database_url: str) -> Any:
    container = build_test_container(database_url)
    yield container.repository.engine
    container.repository.engine.dispose()


def test_an_exercise_is_stored_with_its_checks_and_read_back_identical(engine: Any) -> None:
    bank = SqlExerciseBank(engine)
    item = exercise()
    assert bank.add(item, batch_id="b1", provenance={"author": "x"}, reviews=REVIEWS) is True
    stored = bank.get(item.exercise_id)
    assert stored.exercise == item and stored.status == "active" and stored.batch_id == "b1"
    assert stored.provenance == {"author": "x"}
    assert [r.reviewer_kind for r in bank.reviews(item.exercise_id)] == [
        "blind_solver",
        "rubric_judge",
    ]
    assert bank.dedup_keys() == frozenset({item.dedup_key()})


def test_adding_the_same_exercise_twice_changes_nothing(engine: Any) -> None:
    bank = SqlExerciseBank(engine)
    item = exercise()
    assert bank.add(item, batch_id="b1", provenance={}, reviews=REVIEWS)
    assert not bank.add(item, batch_id="b2", provenance={}, reviews=REVIEWS)
    assert bank.get(item.exercise_id).batch_id == "b1" and len(bank.reviews(item.exercise_id)) == 2


def test_the_same_correct_sentence_cannot_enter_twice(engine: Any) -> None:
    bank = SqlExerciseBank(engine)
    bank.add(exercise(), batch_id="b1", provenance={}, reviews=REVIEWS)
    twin = exercise(distractors=["dem", "der"])
    with pytest.raises(InvalidStateError, match="déjà dans la banque"):
        bank.add(twin, batch_id="b2", provenance={}, reviews=REVIEWS)


def test_the_database_refuses_to_edit_or_delete_the_content(engine: Any) -> None:
    bank = SqlExerciseBank(engine)
    item = exercise()
    bank.add(item, batch_id="b1", provenance={"author": "x"}, reviews=REVIEWS)
    for statement in (
        "UPDATE language_exercises SET payload = '{}'::jsonb",
        "UPDATE language_exercises SET level = 'C1'",
        "UPDATE language_exercises SET provenance = '{}'::jsonb",
        "DELETE FROM language_exercises",
    ):
        with (
            pytest.raises(IntegrityError, match="Immutable language exercise"),
            engine.begin() as c,
        ):
            c.execute(text(statement))
    assert bank.get(item.exercise_id).exercise == item


def test_a_withdrawal_is_final_and_leaves_a_trace(engine: Any) -> None:
    bank = SqlExerciseBank(engine)
    item, other = exercise(), exercise(CORRECTION)
    bank.add(item, batch_id="b1", provenance={}, reviews=REVIEWS)
    bank.add(other, batch_id="b1", provenance={}, reviews=REVIEWS)
    bank.withdraw(item.exercise_id, reason="Deux réponses possibles", actor="Ismail")
    stored = bank.get(item.exercise_id)
    assert stored.status == "withdrawn" and stored.withdrawn_reason == "Deux réponses possibles"
    assert [s.exercise.exercise_id for s in bank.list_active()] == [other.exercise_id]
    assert bank.reviews(item.exercise_id)[-1].reviewer_name == "Ismail"
    with pytest.raises(InvalidStateError, match="déjà retiré"):
        bank.withdraw(item.exercise_id, reason="encore", actor="Ismail")
    for statement in (
        "UPDATE language_exercises SET status = 'active'",
        "UPDATE language_exercises SET withdrawn_reason = 'autre'",
    ):
        with (
            pytest.raises(IntegrityError, match=r"stays withdrawn|Immutable"),
            engine.begin() as c,
        ):
            c.execute(text(statement + f" WHERE id = '{item.exercise_id}'"))


def test_a_withdrawal_needs_a_reason_and_an_author(engine: Any) -> None:
    bank = SqlExerciseBank(engine)
    item = exercise()
    bank.add(item, batch_id="b1", provenance={}, reviews=REVIEWS)
    with pytest.raises(InvalidStateError):
        bank.withdraw(item.exercise_id, reason=" ", actor="Ismail")
    with pytest.raises(NotFoundError):
        bank.withdraw("lx-unknown", reason="x", actor="Ismail")
    with pytest.raises(IntegrityError), engine.begin() as c:  # status and reason go together.
        c.execute(text("UPDATE language_exercises SET status = 'withdrawn'"))


def test_reviews_are_append_only_and_a_learner_report_can_be_added(engine: Any) -> None:
    bank = SqlExerciseBank(engine)
    item = exercise()
    bank.add(item, batch_id="b1", provenance={}, reviews=REVIEWS)
    bank.add_review(item.exercise_id, ExerciseReview("learner_report", "learner", "report", {}))
    assert bank.reviews(item.exercise_id)[-1].decision == "report"
    for statement in (
        "UPDATE language_exercise_reviews SET decision = 'approve'",
        "DELETE FROM language_exercise_reviews",
    ):
        with (
            pytest.raises(IntegrityError, match="Immutable language exercise review"),
            engine.begin() as c,
        ):
            c.execute(text(statement))
    with pytest.raises(NotFoundError):
        bank.add_review("lx-unknown", ExerciseReview("human", "x", "approve", {}))


def test_the_database_checks_the_closed_lists(engine: Any) -> None:
    bank = SqlExerciseBank(engine)
    bank.add(exercise(), batch_id="b1", provenance={}, reviews=REVIEWS)
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO language_exercise_reviews (exercise_id, reviewer_kind, reviewer_name,"
                " decision, payload, created_at) SELECT id, 'oracle', 'x', 'approve', '{}'::jsonb,"
                " now() FROM language_exercises"
            )
        )


def test_a_learner_answer_is_graded_by_code_kept_once_and_never_edited(database_url: str) -> None:
    container = build_test_container(database_url)
    engine = container.repository.engine
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    bank, drills = SqlExerciseBank(engine), SqlDrillStore(engine)
    item = exercise()
    bank.add(item, batch_id="b1", provenance={}, reviews=REVIEWS)
    wrong = (item.answer_index + 1) % len(item.options)
    drill = LanguageDrill(
        id=new_id(),
        learner_id=learner.id,
        request_id="r1",
        exercise_id=item.exercise_id,
        exercise_hash=item.content_hash,
        level=item.level,
        category=item.category,
        chosen_index=wrong,
        correct=grade(item, wrong),
    )
    assert drills.record(drill).correct is False
    retry = replace(drill, id=new_id(), chosen_index=item.answer_index, correct=True)
    assert drills.record(retry).chosen_index == wrong  # The same request is the same answer.
    assert [d.id for d in drills.of_learner(learner.id)] == [drill.id]
    for statement in ("UPDATE language_drills SET correct = true", "DELETE FROM language_drills"):
        with pytest.raises(IntegrityError, match="Immutable language drill"), engine.begin() as c:
            c.execute(text(statement))
    ghost = replace(drill, id=new_id(), request_id="r2", exercise_id="lx-unknown")
    with pytest.raises(InvalidStateError):
        drills.record(ghost)
    engine.dispose()
