"""Deterministic checks on the language errors the evaluator proposes."""

from __future__ import annotations

from ari.application.schemas import LanguageErrorSchema
from ari.application.services.language_errors import verify_language_errors

TURNS = {
    1: "Grüß Gott, ich bin Ihre Ärztin heute. Was bringt Sie zu mir?",
    2: "nennen sie mir bitte ihren vollständigen namen",
    3: "Nennen Sie mich bitte Ihren vollständigen Namen und Ihr Alter.",
    4: "Wie lange Sie haben diese Kopfschmerzen schon?",
}


def error(
    quote: str, correction: str, turns: list[int], category: str = "case"
) -> LanguageErrorSchema:
    return LanguageErrorSchema.model_validate(
        {
            "text": "x",
            "quote": quote,
            "correction": correction,
            "category": category,
            "evidence_turn_sequences": turns,
        }
    )


def test_a_quote_found_in_the_cited_turn_is_kept() -> None:
    verdict = verify_language_errors([error("Nennen Sie mich", "Nennen Sie mir", [3])], TURNS)
    assert [e.quote for e in verdict.kept] == ["Nennen Sie mich"]
    assert verdict.dropped == {}


def test_a_quote_that_is_not_in_the_cited_turn_is_an_invented_evidence() -> None:
    verdict = verify_language_errors([error("Nennen Sie mich", "Nennen Sie mir", [1])], TURNS)
    assert verdict.kept == ()
    assert verdict.dropped == {"not_in_turn": 1}


def test_evidence_is_narrowed_to_the_turns_that_hold_the_quote() -> None:
    verdict = verify_language_errors([error("Was bringt Sie", "Was bringt Sie", [1, 3])], TURNS)
    # Same words after correction: nothing to report, but the narrowing is what is tested next.
    assert verdict.dropped == {"transcriber_only": 1}
    narrowed = verify_language_errors([error("Was bringt Sie", "Was führt Sie", [1, 3])], TURNS)
    assert narrowed.kept[0].evidence_turn_sequences == [1]


def test_a_correction_that_only_changes_what_the_transcriber_did_is_dropped() -> None:
    for correction in (
        "Nennen Sie mir bitte ihren vollständigen Namen",
        "nennen sie mir bitte, ihren vollständigen Namen",
    ):
        verdict = verify_language_errors(
            [error("nennen sie mir bitte ihren vollständigen namen", correction, [2])], TURNS
        )
        # Only a different word (mir/mich) would be an error; here only case and commas differ.
        assert verdict.dropped == {"transcriber_only": 1}
    spaced = verify_language_errors(
        [error("Kopf schmerzen", "Kopfschmerzen", [4], "word_choice")],
        {4: "Wie lange Sie haben diese Kopf schmerzen schon?"},
    )
    assert spaced.dropped == {"transcriber_only": 1}
    assert verify_language_errors(
        [error("Strasse", "Straße", [1], "other")], {1: "Die Strasse ist weit"}
    ).dropped == {"transcriber_only": 1}


def test_the_same_words_reported_twice_are_reported_once() -> None:
    verdict = verify_language_errors(
        [
            error("Nennen Sie mich", "Nennen Sie mir", [3], "word_choice"),
            error("nennen sie mich", "Nennen Sie mir", [3], "case"),
        ],
        TURNS,
    )
    assert [e.category for e in verdict.kept] == ["word_choice"]
    assert verdict.dropped == {"duplicate": 1}


def test_a_correction_that_only_moves_the_words_is_a_word_order_error() -> None:
    verdict = verify_language_errors(
        [error("Wie lange Sie haben", "Wie lange haben Sie", [4], "verb_form")], TURNS
    )
    assert [e.category for e in verdict.kept] == ["word_order"]


def test_a_one_letter_quote_is_not_evidence() -> None:
    verdict = verify_language_errors([error("S", "Sie", [1])], TURNS)
    assert verdict.kept == ()


def test_an_unknown_turn_cannot_carry_a_quote() -> None:
    verdict = verify_language_errors([error("Nennen Sie mich", "Nennen Sie mir", [9])], TURNS)
    assert verdict.dropped == {"not_in_turn": 1}


def test_a_quote_cannot_start_or_end_inside_another_word() -> None:
    turns = {1: "In Ihrer Familie?"}
    assert verify_language_errors([error("Ihre", "Ihrer", [1])], turns).dropped == {
        "not_in_turn": 1
    }
    assert verify_language_errors([error("hrer", "Ihrer", [1])], turns).dropped == {
        "not_in_turn": 1
    }
    assert verify_language_errors([error("In Ihrer", "In Ihre", [1])], turns).kept


def test_two_different_passages_of_one_turn_are_two_errors() -> None:
    turns = {1: "Darf ich Ihren Namen und Ihren Alter erfahren?"}
    verdict = verify_language_errors(
        [
            error("Ihren Alter", "Ihr Alter", [1], "gender"),
            error("Ihren Namen", "Ihr Namen", [1], "gender"),
        ],
        turns,
    )
    assert [e.quote for e in verdict.kept] == ["Ihren Alter", "Ihren Namen"]


def test_a_quote_that_holds_or_is_held_by_an_earlier_one_is_the_same_passage() -> None:
    verdict = verify_language_errors(
        [
            error("Nennen Sie mich", "Nennen Sie mir", [3], "case"),
            error("mich", "mir", [3], "word_choice"),
            error("Nennen Sie mich bitte", "Nennen Sie mir bitte", [3], "case"),
        ],
        TURNS,
    )
    assert [e.quote for e in verdict.kept] == ["Nennen Sie mich"]
    assert verdict.dropped == {"duplicate": 2}


def test_words_that_only_look_alike_are_not_the_same_passage() -> None:
    turns = {1: "Ich habe ein Termin mit Schmerzen in beiden Bein."}
    verdict = verify_language_errors(
        [
            error("ein", "einen", [1], "gender"),
            error("Bein", "Beinen", [1], "case"),
        ],
        turns,
    )
    assert [e.quote for e in verdict.kept] == ["ein", "Bein"]


def test_two_occurrences_of_one_word_are_two_passages() -> None:
    turns = {1: "Ich habe ein Termin und fahre mit ein Auto."}
    verdict = verify_language_errors(
        [
            error("ein", "einen", [1], "gender"),
            error("mit ein Auto", "mit einem Auto", [1], "case"),
        ],
        turns,
    )
    assert [e.quote for e in verdict.kept] == ["ein", "mit ein Auto"]
    # The same word reported three times in a turn that has it twice: the third is a repeat.
    triple = verify_language_errors([error("ein", "einen", [1])] * 3, turns)
    assert len(triple.kept) == 2 and triple.dropped == {"duplicate": 1}
