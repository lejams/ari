"""What the transcriber heard against the sentence the voice was given.

Both sides are normalized the same way before comparing: lower case, numbers written out in
German words ("38,5" and "achtunddreißig Komma fünf" are the same words), a few unit
abbreviations spelled out, hyphens joined ("Salbutamol-Spray" and "Salbutamolspray" match),
punctuation dropped. What remains is pronunciation, or a transcription error: a sentence with
a missed critical term is one a German speaker has to listen to, not a verdict on the voice.

Outcomes, per sentence:
- `ok`: every word heard as written;
- `listen`: every critical term heard, but some other words differ;
- `critical_missed`: a critical term was not heard (a number, a dose, a drug, a term);
- `failed`: no audio, or no transcript.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from ari.evals.voice.scoring import word_errors

Outcome = Literal["ok", "listen", "critical_missed", "failed"]

_UNITS = (
    "null",
    "eins",
    "zwei",
    "drei",
    "vier",
    "fünf",
    "sechs",
    "sieben",
    "acht",
    "neun",
    "zehn",
    "elf",
    "zwölf",
    "dreizehn",
    "vierzehn",
    "fünfzehn",
    "sechzehn",
    "siebzehn",
    "achtzehn",
    "neunzehn",
)
_TENS = (
    *("", "", "zwanzig", "dreißig", "vierzig"),
    *("fünfzig", "sechzig", "siebzig", "achtzig", "neunzig"),
)
_ABBREVIATIONS = {"mg": "milligramm", "ml": "milliliter", "kg": "kilogramm"}
_SYMBOLS = (("°c", "grad"), ("°", "grad"), ("%", "prozent"))
_NUMBER = re.compile(r"\d+(?:,\d+)?")


def german_number(value: int) -> str:
    """0 to 999 999 in German words, as one word ("hunderteins", "achtunddreißig")."""
    if value < 0 or value >= 1_000_000:
        return str(value)
    if value < 20:
        return _UNITS[value]
    if value < 100:
        tens, unit = divmod(value, 10)
        if not unit:
            return _TENS[tens]
        return f"{'ein' if unit == 1 else _UNITS[unit]}und{_TENS[tens]}"
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        head = f"{'ein' if hundreds == 1 else _UNITS[hundreds]}hundert"
        return head + (german_number(rest) if rest else "")
    thousands, rest = divmod(value, 1000)
    head = f"{'ein' if thousands == 1 else german_number(thousands)}tausend"
    return head + (german_number(rest) if rest else "")


def _spell(match: re.Match[str]) -> str:
    whole, _, decimals = match.group().partition(",")
    words = german_number(int(whole))
    if decimals:
        words += " komma " + " ".join(_UNITS[int(digit)] for digit in decimals)
    return f" {words} "


def normalize(text: str) -> list[str]:
    # Case folded after spelling, so that "ß" becomes "ss" on both sides.
    spelled = _NUMBER.sub(_spell, text.replace("-", "")).casefold()
    for symbol, word in _SYMBOLS:
        spelled = spelled.replace(symbol, f" {word} ")
    for short, word in _ABBREVIATIONS.items():
        spelled = re.sub(rf"\b{short}\b", word, spelled)
    return re.findall(r"\w+", spelled)


def _contains(heard: Sequence[str], term: Sequence[str]) -> bool:
    size = len(term)
    return any(list(heard[i : i + size]) == list(term) for i in range(len(heard) - size + 1))


def missed_terms(critical: Sequence[Sequence[str]], heard: str) -> list[str]:
    """The first spelling of every critical term none of whose spellings was heard."""
    words = normalize(heard)
    return [
        spellings[0]
        for spellings in critical
        if not any(_contains(words, normalize(spelling)) for spelling in spellings)
    ]


def score(
    text: str, critical: Sequence[Sequence[str]], heard: str | None
) -> tuple[Outcome, int, int, list[str]]:
    """(outcome, word errors, words of the sentence, critical terms missed)."""
    reference = " ".join(normalize(text))
    total = len(reference.split())
    if heard is None or not heard.strip():
        return "failed", total, total, [spellings[0] for spellings in critical]
    errors = word_errors(reference, " ".join(normalize(heard)))
    missed = missed_terms(critical, heard)
    if missed:
        return "critical_missed", errors, total, missed
    return ("listen" if errors else "ok"), errors, total, missed
