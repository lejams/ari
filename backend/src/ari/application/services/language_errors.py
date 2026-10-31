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
  gave it.

What the code cannot decide (is it really wrong German? gender or case?) stays with the model,
guided by the prompt and measured by `ari.evals`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ari.application.schemas import LanguageErrorSchema

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


@dataclass(frozen=True, slots=True)
class LanguageErrorVerdict:
    kept: tuple[LanguageErrorSchema, ...]
    dropped: Mapping[str, int]  # Reason -> count: not_in_turn, transcriber_only, duplicate.


def verify_language_errors(
    errors: Sequence[LanguageErrorSchema], learner_text_by_turn: Mapping[int, str]
) -> LanguageErrorVerdict:
    kept: list[LanguageErrorSchema] = []
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
        # The same passage: the words of the quote overlap words already reported in a turn.
        if any(
            start < old_end and old_start < end
            for turn, where in found.items()
            for start, end in where
            for old_start, old_end in seen.get(turn, ())
        ):
            dropped["duplicate"] += 1
            continue
        for turn, where in found.items():
            seen.setdefault(turn, []).extend(where)
        category = error.category
        spoken, corrected = words(error.quote), words(error.correction)
        if spoken != corrected and Counter(spoken) == Counter(corrected):
            category = "word_order"
        kept.append(
            error.model_copy(update={"category": category, "evidence_turn_sequences": list(turns)})
        )
    return LanguageErrorVerdict(kept=tuple(kept), dropped=dict(dropped))
