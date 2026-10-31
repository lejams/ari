"""Deterministic checks on the language errors the evaluator proposes ("the LLM proposes, the
code decides").

The model quotes the learner's words and the corrected words. The code then checks, without
any model, what it can check:

- the quote must occur in a cited turn, otherwise the evidence is invented and the error is
  dropped, and the evidence is narrowed to the turns that really contain it;
- a correction that only changes what the transcriber produced (capitalisation, punctuation,
  hyphens, spaces, ss/ß) is not an error of the learner and is dropped;
- the same words reported twice are reported once;
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


@dataclass(frozen=True, slots=True)
class LanguageErrorVerdict:
    kept: tuple[LanguageErrorSchema, ...]
    dropped: Mapping[str, int]  # Reason -> count: not_in_turn, transcriber_only, duplicate.


def verify_language_errors(
    errors: Sequence[LanguageErrorSchema], learner_text_by_turn: Mapping[int, str]
) -> LanguageErrorVerdict:
    kept: list[LanguageErrorSchema] = []
    dropped: Counter[str] = Counter()
    seen: set[tuple[tuple[int, ...], str]] = set()
    for error in errors:
        quote = squeezed(error.quote)
        turns = tuple(
            sorted(
                turn
                for turn in set(error.evidence_turn_sequences)
                if turn in learner_text_by_turn and quote in squeezed(learner_text_by_turn[turn])
            )
        )
        if len(quote) < _MIN_QUOTE_CHARACTERS or not turns:
            dropped["not_in_turn"] += 1
            continue
        if quote == squeezed(error.correction):
            dropped["transcriber_only"] += 1
            continue
        key = (turns, quote)
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        category = error.category
        spoken, corrected = words(error.quote), words(error.correction)
        if spoken != corrected and Counter(spoken) == Counter(corrected):
            category = "word_order"
        kept.append(
            error.model_copy(update={"category": category, "evidence_turn_sequences": list(turns)})
        )
    return LanguageErrorVerdict(kept=tuple(kept), dropped=dict(dropped))
