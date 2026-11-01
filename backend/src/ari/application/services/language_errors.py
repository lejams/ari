"""Deterministic checks on the language errors the evaluator proposes ("the LLM proposes, the
code decides").

The model quotes the learner's words and the corrected words. The code then checks, without
any model, what it can check:

- the quote must occur in a cited turn, otherwise the evidence is invented and the error is
  dropped, and the evidence is narrowed to the turns that really contain it;
- a correction that only changes what the transcriber produced (capitalisation, punctuation,
  hyphens, spaces, ss/ß) is not an error of the learner and is dropped;
- the same passage reported twice (quotes covering some of the same words of a turn) is reported
  once; two different passages of one turn stay two errors;
- a correction that only moves the same words is a word-order error, whatever label the model
  gave it;
- a separable verb said in one piece (`einnehmen Sie`) that the correction splits into its verb
  and its prefix (`nehmen Sie ... ein`) is a verb-form error, not a word-order one.

What the code cannot decide (is it really wrong German? gender or case?) stays with the model,
guided by the prompt and measured by `ari.evals`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ari.application.schemas import CodeSwitchSchema, LanguageErrorSchema

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_MIN_QUOTE_CHARACTERS = 2


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("ß", "ss")


def words(text: str) -> list[str]:
    """Words as spoken: case, punctuation and ß/ss differences removed."""
    return _WORD.findall(_fold(text))


def squeezed(text: str) -> str:
    """Letters only: what stays the same whatever the transcriber did with spaces and hyphens."""
    return "".join(words(text))


def spans(quote: str, text: str) -> list[tuple[int, int]]:
    """Where the quote stands in the text, as ranges of word positions `[start, end)`.

    A quote starts at the beginning of a word and ends at the end of a word. Spaces and hyphens
    are ignored, so a compound split by the transcriber still matches, but a quote cannot start or
    end inside another word (`Ihre` is not in `Ihrer`).
    """
    wanted = squeezed(quote)
    if not wanted:
        return []
    tokens = words(text)
    found: list[tuple[int, int]] = []
    for start in range(len(tokens)):
        joined = ""
        for end in range(start, len(tokens)):
            joined += tokens[end]
            if joined == wanted:
                found.append((start, end + 1))
            if len(joined) >= len(wanted):
                break
    return found


def splits_a_word(spoken: Sequence[str], corrected: Sequence[str]) -> bool:
    """Whether a spoken word is, in the correction, cut into a prefix and a verb.

    `einnehmen` -> `nehmen` and `ein`: the spoken word is gone from the correction and both parts
    are added to it. Words are counted, not just listed, so a prefix that the quote already holds
    elsewhere (`einnehmen Sie ein Medikament` -> `nehmen Sie ein Medikament ein`) is still an added
    word. Prefix and stem are at least two letters long, so a short function word never counts.
    """
    added = Counter(corrected) - Counter(spoken)
    gone = Counter(spoken) - Counter(corrected)
    return any(
        word in gone
        and any(
            len(prefix) >= 2
            and len(stem) >= 2
            and prefix + stem == word
            and (prefix != stem or added[prefix] >= 2)
            for prefix in added
            for stem in added
        )
        for word in spoken
    )


@dataclass(frozen=True, slots=True)
class LanguageErrorVerdict:
    kept: tuple[LanguageErrorSchema, ...]
    dropped: Mapping[str, int]  # Reason -> count: not_in_turn, transcriber_only, duplicate.
    # For each kept error, the word range `[start, end)` it was given in each of its turns: the
    # occurrence the verification attributed to it (a quote can occur twice in a turn).
    placed: tuple[Mapping[int, tuple[int, int]], ...] = ()


def verify_language_errors(
    errors: Sequence[LanguageErrorSchema], learner_text_by_turn: Mapping[int, str]
) -> LanguageErrorVerdict:
    kept: list[LanguageErrorSchema] = []
    placed: list[Mapping[int, tuple[int, int]]] = []
    dropped: Counter[str] = Counter()
    seen: dict[int, list[tuple[int, int]]] = {}  # Turn -> word ranges already reported.
    for error in errors:
        quote = squeezed(error.quote)
        found = {
            turn: spans(error.quote, learner_text_by_turn[turn])
            for turn in set(error.evidence_turn_sequences)
            if turn in learner_text_by_turn
        }
        found = {turn: where for turn, where in found.items() if where}
        turns = tuple(sorted(found))
        if len(quote) < _MIN_QUOTE_CHARACTERS or not turns:
            dropped["not_in_turn"] += 1
            continue
        if quote == squeezed(error.correction):
            dropped["transcriber_only"] += 1
            continue
        # The same passage: its words overlap words already reported in the turn. A quote that
        # occurs twice in a turn stands for the first occurrence not yet reported, so two
        # corrections of two occurrences of one word are two errors.
        free: dict[int, tuple[int, int]] = {}
        for turn, where in found.items():
            taken = seen.get(turn, [])
            span = next(
                (
                    (start, end)
                    for start, end in where
                    if not any(start < old_end and old_start < end for old_start, old_end in taken)
                ),
                None,
            )
            if span is not None:
                free[turn] = span
        if not free:
            dropped["duplicate"] += 1
            continue
        turns = tuple(sorted(free))
        for turn, span in free.items():
            seen.setdefault(turn, []).append(span)
        category = error.category
        spoken, corrected = words(error.quote), words(error.correction)
        if spoken != corrected and Counter(spoken) == Counter(corrected):
            category = "word_order"
        elif category == "word_order" and splits_a_word(spoken, corrected):
            category = "verb_form"
        kept.append(
            error.model_copy(update={"category": category, "evidence_turn_sequences": list(turns)})
        )
        placed.append(dict(free))
    return LanguageErrorVerdict(kept=tuple(kept), dropped=dict(dropped), placed=tuple(placed))


def verify_code_switches(
    switches: Sequence[CodeSwitchSchema], learner_text_by_turn: Mapping[int, str]
) -> tuple[CodeSwitchSchema, ...]:
    """Keep the code switches the transcript can back.

    The fragment must stand in the cited turn, and the term the learner "needed" must differ from
    what they said: a model that proposes `Migräne` for `Migräne` has found German, not a switch.
    """
    kept: list[CodeSwitchSchema] = []
    for switch in switches:
        text = learner_text_by_turn.get(switch.turn)
        if text is None or not spans(switch.fragment, text):
            continue
        if switch.intended_term and squeezed(switch.intended_term) == squeezed(switch.fragment):
            continue
        kept.append(switch)
    return tuple(kept)


Cell = tuple[int, int]  # (turn, word position)


def _cells_of(turn: int, span: tuple[int, int]) -> set[Cell]:
    return {(turn, position) for position in range(span[0], span[1])}


def _cluster[T](
    passes: Sequence[Sequence[T]], cells_of: Callable[[T], set[Cell]]
) -> list[list[tuple[int, T]]]:
    """Group what several passes reported about the same words: one member per pass at most."""
    clusters: list[tuple[set[Cell], list[tuple[int, T]]]] = []
    for index, items in enumerate(passes):
        for item in items:
            cells = cells_of(item)
            home = next(
                (
                    entry
                    for entry in clusters
                    if entry[0] & cells and all(owner != index for owner, _ in entry[1])
                ),
                None,
            )
            if home is None:
                clusters.append((set(cells), [(index, item)]))
            else:
                home[0].update(cells)
                home[1].append((index, item))
    return [members for _, members in clusters]


def majority(passes: int) -> int:
    """How many of the passes must report something for it to stand: more than half."""
    return passes // 2 + 1


@dataclass(frozen=True, slots=True)
class PlacedError:
    """A verified language error and where it stands (see `LanguageErrorVerdict.placed`)."""

    error: LanguageErrorSchema
    placed: Mapping[int, tuple[int, int]]


def placed_errors(verdict: LanguageErrorVerdict) -> tuple[PlacedError, ...]:
    return tuple(PlacedError(e, p) for e, p in zip(verdict.kept, verdict.placed, strict=True))


def consolidate_language_errors(
    passes: Sequence[Sequence[PlacedError]],
) -> tuple[LanguageErrorSchema, ...]:
    """The language errors that a majority of independent passes report on the same words.

    Each pass is already verified (`verify_language_errors`). A single pass sometimes sees an
    error and sometimes does not, so what the evaluator reports changes from one run to the next;
    keeping what most passes see removes that noise without any new judgement by the code.

    Votes are counted on the exact words each pass was given, per turn. The errors returned are
    always one of the passes' own, whole: its quote and correction stand in every turn it cites,
    so a turn a majority agrees on but that this pass did not cite is reported by another pass's
    error (the category is the one most passes gave, the earliest pass on a tie). The same words
    are never reported twice: a result that overlaps one already kept is dropped.
    """
    needed = majority(len(passes))
    results: list[tuple[int, Cell, PlacedError]] = []
    for members in _cluster(
        passes,
        lambda item: {c for turn, span in item.placed.items() for c in _cells_of(turn, span)},
    ):
        votes: Counter[int] = Counter()
        for _, member in members:
            votes.update(member.placed.keys())
        remaining = {turn for turn, count in votes.items() if count >= needed}
        counts = Counter(member.error.category for _, member in members)
        ranked = sorted(
            (member for _, member in members), key=lambda member: -counts[member.error.category]
        )  # Stable: the earliest pass first among equal categories.
        while remaining:
            best = next((m for m in ranked if remaining & set(m.placed)), None)
            if best is None:
                break
            turns = sorted(remaining & set(best.placed))
            remaining -= set(turns)
            position = min((turn, best.placed[turn][0]) for turn in turns)
            placed = {turn: best.placed[turn] for turn in turns}
            results.append(
                (min(votes[t] for t in turns), position, PlacedError(best.error, placed))
            )
    kept: list[tuple[Cell, LanguageErrorSchema]] = []
    taken: set[Cell] = set()
    # The best supported first, so that when results overlap the one most passes agree on stays.
    # The overlap is checked turn by turn: a turn already reported is dropped from the evidence,
    # the error's other turns stay.
    for _, _, result in sorted(results, key=lambda entry: (-entry[0], entry[1])):
        free = {
            turn: span for turn, span in result.placed.items() if not _cells_of(turn, span) & taken
        }
        if not free:
            continue
        for turn, span in free.items():
            taken |= _cells_of(turn, span)
        turns = sorted(free)
        error = result.error.model_copy(update={"evidence_turn_sequences": turns})
        kept.append((min((turn, free[turn][0]) for turn in turns), error))
    return tuple(error for _, error in sorted(kept, key=lambda entry: entry[0]))


def _switch_cells(switch: CodeSwitchSchema, learner_text_by_turn: Mapping[int, str]) -> set[Cell]:
    found = spans(switch.fragment, learner_text_by_turn.get(switch.turn, ""))
    return _cells_of(switch.turn, found[0]) if found else set()


def consolidate_code_switches(
    passes: Sequence[Sequence[CodeSwitchSchema]], learner_text_by_turn: Mapping[int, str]
) -> tuple[CodeSwitchSchema, ...]:
    """The code switches that a majority of passes report on the same words.

    When passes cite a different stretch of the same words (the word, or the whole sentence), the
    shortest fragment stands: it is the one that names the foreign word.
    """
    needed = majority(len(passes))
    kept: list[tuple[Cell, CodeSwitchSchema]] = []
    for members in _cluster(passes, lambda switch: _switch_cells(switch, learner_text_by_turn)):
        if len(members) < needed:
            continue
        chosen = min((switch for _, switch in members), key=lambda switch: len(switch.fragment))
        position = min(
            cell for _, switch in members for cell in _switch_cells(switch, learner_text_by_turn)
        )
        kept.append((position, chosen))
    return tuple(switch for _, switch in sorted(kept, key=lambda entry: entry[0]))
