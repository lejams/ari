"""Deterministic checks on the language errors the evaluator proposes."""

from __future__ import annotations

from ari.application.schemas import CodeSwitchSchema, LanguageErrorSchema
from ari.application.services.language_errors import (
    consolidate_code_switches,
    consolidate_language_errors,
    placed_errors,
    splits_a_word,
    verify_code_switches,
    verify_language_errors,
)

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


def test_a_separable_verb_said_in_one_piece_is_a_verb_form_error_not_a_word_order_one() -> None:
    turns = {1: "Was einnehmen Sie gegen die Schmerzen?"}
    verdict = verify_language_errors(
        [
            error(
                "Was einnehmen Sie",
                "Was nehmen Sie ein",
                [1],
                category="word_order",
            )
        ],
        turns,
    )
    assert [e.category for e in verdict.kept] == ["verb_form"]


def test_a_word_order_error_without_a_split_word_stays_word_order() -> None:
    turns = {1: "Wie lange Sie haben diese Kopfschmerzen schon?"}
    verdict = verify_language_errors(
        [
            error(
                "Wie lange Sie haben",
                "Wie lange haben Sie",
                [1],
                category="word_order",
            )
        ],
        turns,
    )
    assert [e.category for e in verdict.kept] == ["word_order"]
    assert not splits_a_word(["er", "geht"], ["er", "geht", "weg"])


def test_a_short_function_word_is_not_a_prefix() -> None:
    # `zu` + `viel` would spell `zuviel`: only prefixes and stems of two letters or more count,
    # and only when the spoken word is gone from the correction.
    assert splits_a_word(["zuviel"], ["zu", "viel"])
    assert not splits_a_word(["zuviel"], ["zuviel", "zu", "viel"])
    assert not splits_a_word(["ab"], ["a", "b"])


def test_a_prefix_already_in_the_quote_still_counts_as_an_added_word() -> None:
    assert splits_a_word(
        ["einnehmen", "sie", "ein", "medikament"], ["nehmen", "sie", "ein", "medikament", "ein"]
    )
    assert not splits_a_word(["nehmen", "sie", "ein"], ["nehmen", "sie", "ein", "ein"])


def switch(turn: int, fragment: str, intended: str | None) -> CodeSwitchSchema:
    return CodeSwitchSchema(turn=turn, fragment=fragment, intended_term=intended)


def test_a_code_switch_whose_intended_term_is_what_was_said_is_dropped() -> None:
    turns = {1: "Leidet jemand aus Ihrer Familie ebenfalls an Migräne?"}
    assert verify_code_switches([switch(1, "Migräne", "Migräne")], turns) == ()
    assert verify_code_switches([switch(1, "migraene", "Migräne")], turns) == ()


def test_a_code_switch_found_in_its_turn_with_another_term_is_kept() -> None:
    turns = {1: "Haben Sie mal de tête seit gestern?"}
    kept = verify_code_switches([switch(1, "mal de tête", "Kopfschmerzen")], turns)
    assert [item.intended_term for item in kept] == ["Kopfschmerzen"]
    assert verify_code_switches([switch(1, "mal de tête", None)], turns) == tuple(
        [switch(1, "mal de tête", None)]
    )


def test_a_code_switch_not_in_the_cited_turn_is_dropped() -> None:
    turns = {1: "Haben Sie Fieber?", 2: "Und mal de tête?"}
    assert verify_code_switches([switch(1, "mal de tête", "Kopfschmerzen")], turns) == ()
    assert verify_code_switches([switch(9, "mal de tête", "Kopfschmerzen")], turns) == ()


def vote(
    passes: list[list[LanguageErrorSchema]], turns: dict[int, str] | None = None
) -> tuple[LanguageErrorSchema, ...]:
    text = turns or TURNS
    return consolidate_language_errors(
        [placed_errors(verify_language_errors(errors, text)) for errors in passes]
    )


def test_an_error_seen_by_two_passes_out_of_three_stands_and_one_seen_once_does_not() -> None:
    seen_twice = error("Nennen Sie mich", "Nennen Sie mir", [3])
    seen_once = error("Wie lange Sie haben", "Wie lange haben Sie", [4], "word_order")
    kept = vote([[seen_twice, seen_once], [seen_twice], []])
    assert [e.quote for e in kept] == ["Nennen Sie mich"]


def test_the_same_words_quoted_differently_count_as_one_error() -> None:
    short = error("Wie lange Sie", "Wie lange haben Sie", [4], "word_order")
    long = error("Wie lange Sie haben diese", "Wie lange haben Sie diese", [4], "word_order")
    kept = vote([[short], [long], []])
    assert len(kept) == 1


def test_the_category_most_passes_gave_wins_and_the_earliest_pass_breaks_a_tie() -> None:
    first = error("Nennen Sie mich", "Nennen Sie mir", [3], "case")
    second = error("Nennen Sie mich", "Nennen Sie mir", [3], "word_choice")
    third = error("Nennen Sie mich", "Nennen Sie mir", [3], "word_choice")
    assert vote([[first], [second], [third]])[0].category == ("word_choice")
    assert vote([[first], [second]])[0].category == "case"


def test_two_errors_of_one_pass_are_not_merged_into_one() -> None:
    one = error("Nennen Sie mich", "Nennen Sie mir", [3])
    two = error("Ihr Alter", "Ihrem Alter", [3])
    both = [one, two]
    assert len(vote([both, both, both])) == 2


def test_a_single_pass_is_its_own_majority() -> None:
    only = error("Nennen Sie mich", "Nennen Sie mir", [3])
    assert vote([[only]]) == (only,)


def test_a_code_switch_needs_a_majority_and_the_shortest_fragment_stands() -> None:
    word = switch(2, "namen", "Namen")
    sentence = switch(2, "ihren vollständigen namen", "Name")
    other = switch(4, "Kopfschmerzen", "Kopfschmerzen ")
    kept = consolidate_code_switches([[word, other], [sentence], []], TURNS)
    assert kept == (word,)


def test_two_occurrences_of_a_word_are_voted_separately() -> None:
    turns = {1: "Ich habe ein Termin und fahre mit ein Auto."}
    first = error("ein", "einen", [1])
    second = error("mit ein Auto", "mit einem Auto", [1])
    # Each passage was seen by one pass only: neither has a majority.
    assert vote([[first], [second], []], turns) == ()
    # The same passage seen twice stands, and the other one still does not.
    kept = vote([[first], [first, second], []], turns)
    assert [e.quote for e in kept] == ["ein"]


def test_the_evidence_is_narrowed_to_the_turns_a_majority_agrees_on() -> None:
    turns = {1: "Nennen Sie mich bitte.", 2: "Nennen Sie mich noch einmal."}
    both = error("Nennen Sie mich", "Nennen Sie mir", [1, 2])
    only_first = error("Nennen Sie mich", "Nennen Sie mir", [1])
    kept = vote([[both], [only_first], []], turns)
    assert len(kept) == 1 and kept[0].evidence_turn_sequences == [1]
