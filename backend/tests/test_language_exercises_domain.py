"""The exercise schema, the options the code builds, and each mechanical check."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from pydantic import ValidationError

from ari.domain.language_exercises import (
    ExerciseCandidate,
    LanguageExercise,
    assemble,
    grade,
    mechanical_issues,
)

CLOZE: dict[str, Any] = {
    "level": "B1",
    "category": "gender",
    "format": "cloze",
    "stem": "Haben Sie ___ Tablette heute schon genommen?",
    "key": "die",
    "distractors": ["den", "das"],
    "explanation_fr": "« Tablette » est féminin : à l'accusatif, l'article est « die ».",
    "setting": "anamnèse",
}
CORRECTION: dict[str, Any] = {
    "level": "B1",
    "category": "case",
    "format": "correct_sentence",
    "stem": "Ich habe mit den Arzt gesprochen.",
    "key": "Ich habe mit dem Arzt gesprochen.",
    "distractors": ["Ich habe mit der Arzt gesprochen.", "Ich habe mit dem Arztes gesprochen."],
    "explanation_fr": "« mit » est toujours suivi du datif : « dem Arzt ».",
    "setting": "entretien",
}
REGISTER: dict[str, Any] = {
    "level": "B2",
    "category": "register",
    "format": "register_choice",
    "context_fr": "Vous parlez à une patiente âgée que vous ne connaissez pas.",
    "stem": "Hast du heute Morgen schon was gegessen?",
    "key": "Haben Sie heute Morgen schon etwas gegessen?",
    "distractors": ["Hast du heute Morgen schon etwas gegessen?", "Na, schon was geknabbert?"],
    "explanation_fr": "À une patiente inconnue, on dit « Sie » sur un ton neutre et poli.",
    "setting": "accueil",
}


def candidate(base: dict[str, Any], **changes: Any) -> ExerciseCandidate:
    return assemble(**{**base, **changes})


@pytest.mark.parametrize("base", [CLOZE, CORRECTION, REGISTER])
def test_a_good_exercise_passes_and_has_a_stable_identity(base: dict[str, Any]) -> None:
    first, second = candidate(base), candidate(base)
    assert mechanical_issues(first) == []
    exercise = LanguageExercise.model_validate_json(first.model_dump_json())
    assert (
        exercise.exercise_id
        == LanguageExercise.model_validate_json(second.model_dump_json()).exercise_id
    )
    assert exercise.exercise_id.startswith("lx-") and exercise.skill in {
        "grammar",
        "vocabulary",
        "register",
    }
    assert exercise.key == base["key"]


def test_the_code_draws_the_options_so_the_key_does_not_sit_in_one_place() -> None:
    positions = Counter(
        candidate(CLOZE, stem=f"Haben Sie {n} ___ Tablette heute schon genommen?").answer_index
        for n in range(60)
    )
    assert set(positions) == {0, 1, 2} and min(positions.values()) >= 8


def test_the_correct_sentence_fills_the_blank_of_a_cloze() -> None:
    assert candidate(CLOZE).correct_sentence() == "Haben Sie die Tablette heute schon genommen?"
    assert candidate(CORRECTION).correct_sentence() == CORRECTION["key"]


def test_the_same_correct_sentence_is_the_same_exercise() -> None:
    other = candidate(CLOZE, distractors=["dem", "der"], explanation_fr="Autre explication ici.")
    assert other.dedup_key() == candidate(CLOZE).dedup_key()
    assert other.content_hash != candidate(CLOZE).content_hash


@pytest.mark.parametrize(
    ("changes", "issue"),
    [
        ({"distractors": ["den"]}, "option_count"),
        ({"distractors": ["den", "Den "]}, "duplicate_options"),
        ({"distractors": ["die", "das"]}, "duplicate_options"),
        ({"stem": "Haben Sie die Tablette heute schon genommen?"}, "blank_count"),
        ({"stem": "Haben Sie ___ Tablette ___ schon genommen?"}, "blank_count"),
        ({"distractors": ["___", "das"]}, "blank_in_option"),
        ({"stem": "Haben Sie ____ Tablette heute schon genommen?"}, "blank_count"),  # Four.
        ({"stem": "Haben Sie ___ Tablette heute schon _ genommen?"}, "blank_count"),
        ({"distractors": ["de\u00adn", "das"]}, "unexpected_characters"),  # Soft hyphen.
        ({"stem": "Zu kurz ___"}, "stem_length"),
        ({"explanation_fr": "Court."}, "explanation_length"),
        ({"stem": "Haben Sie ___ Tablette <b>schon</b> genommen?"}, "markup_or_link"),
        ({"stem": "Haben Sie ___ Tablette schon genommen? www.example.de"}, "markup_or_link"),
        ({"stem": "Haben Sie ___ 錠剤 heute schon genommen?"}, "unexpected_characters"),
        ({"stem": "Schreiben Sie ___ Tablette an dr.weber@example.de, bitte."}, "contact_details"),
        ({"category": "word_order"}, "format_not_allowed_for_category"),
        ({"setting": "x" * 61}, None),
    ],
)
def test_each_mechanical_check_names_its_problem(
    changes: dict[str, Any], issue: str | None
) -> None:
    if issue is None:  # Structure that the schema itself refuses.
        with pytest.raises(ValidationError):
            candidate(CLOZE, **changes)
        return
    assert issue in mechanical_issues(candidate(CLOZE, **changes))


def test_a_trailing_newline_is_not_an_allowed_character() -> None:
    raw = candidate(CLOZE).model_dump(mode="json")
    padded = ExerciseCandidate.model_validate_json(
        json.dumps({**raw, "options": [o + "\n" for o in raw["options"]]})
    )
    assert "unexpected_characters" in mechanical_issues(padded)


def test_a_correction_needs_something_to_correct_and_close_options() -> None:
    assert "key_equals_stem" in mechanical_issues(candidate(CORRECTION, stem=CORRECTION["key"]))
    assert "blank_in_sentence" in mechanical_issues(
        candidate(CORRECTION, stem="Ich habe mit ___ Arzt gesprochen.")
    )
    far = candidate(CORRECTION, distractors=["Guten Abend allerseits.", "Das Wetter ist schön."])
    assert "options_too_different" in mechanical_issues(far)


def test_a_register_choice_needs_its_situation() -> None:
    assert "context_missing" in mechanical_issues(candidate(REGISTER, context_fr=None))
    assert "format_not_allowed_for_category" in mechanical_issues(
        candidate(REGISTER, category="case")
    )


def test_an_answer_index_outside_the_options_is_refused() -> None:
    broken = candidate(CLOZE).model_copy(update={"answer_index": 7})
    assert mechanical_issues(broken) == ["answer_out_of_range"]
    with pytest.raises(ValidationError, match="answer_out_of_range"):
        LanguageExercise.model_validate_json(broken.model_dump_json())


def test_an_exercise_that_fails_a_check_is_not_a_language_exercise() -> None:
    with pytest.raises(ValidationError, match="duplicate_options"):
        LanguageExercise.model_validate_json(
            candidate(CLOZE, distractors=["die", "das"]).model_dump_json()
        )


def test_grading_is_a_comparison_of_indexes() -> None:
    exercise = candidate(CLOZE)
    right = exercise.answer_index
    assert grade(exercise, right) is True
    assert all(grade(exercise, i) is False for i in range(3) if i != right)
    with pytest.raises(ValueError, match="hors des options"):
        grade(exercise, 3)
