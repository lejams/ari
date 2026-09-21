"""Deterministic text matching shared by scoring, practice and the lexicon. No NLP."""

import re
import unicodedata


def normalize_answer(text: str) -> str:
    """Only case, whitespace and terminal punctuation; never remove negation or numbers."""
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    return re.sub(r"\s+", " ", text).rstrip(".!? ")


def phrase_present(phrase: str, text: str) -> bool:
    """Whole-phrase match, case and whitespace normalized, on word boundaries."""
    normalized = " ".join(text.casefold().split())
    expected = " ".join(phrase.casefold().split())
    return re.search(r"(?<!\w)" + re.escape(expected) + r"(?!\w)", normalized) is not None


def term_used(term: str, text: str, inflection_letters: int = 3) -> bool:
    """A term counts as used when a token starts with it, allowing a short ending.

    German nouns and verbs inflect (Schmerz → Schmerzen, husten → hustet). This is a
    lexical heuristic, not a judgement of correct usage.
    """
    normalized = " ".join(text.casefold().split())
    expected = " ".join(term.casefold().split())
    if not expected:
        return False
    pattern = r"(?<!\w)" + re.escape(expected) + r"\w{0," + str(inflection_letters) + r"}(?!\w)"
    return re.search(pattern, normalized) is not None
