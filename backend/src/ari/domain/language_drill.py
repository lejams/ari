"""The remediation drill "Votre phrase" (`language-drill-v1`): the learner's own errors, replayed.

Nothing is generated. The evaluator already quoted the learner's words and corrected them, and
`verify_language_errors` checked the quote against the transcript. A drill shows that quote again,
a few sessions later, and asks the learner to say it right. The code compares the answer with the
evaluator's correction (case, spacing and final punctuation aside) and never calls a miss a failure:
a different answer may be another correct form, so it is "not matched", the correction is shown and
the learner judges for themselves.

Pure functions and records. The learner model and the drill service both build their candidates
here, so what the programme announces is exactly what a drill will contain.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Literal

from ari.domain.models import ConversationSession, SessionStatus, utc_now
from ari.domain.text import normalize_answer

DRILL_VERSION = "language-drill-v1"
WINDOW_SESSIONS = 5  # the learner model's window of recent completed sessions
RECURRING_MIN = 2  # errors of one category in the window before it becomes the focus
DRILL_ITEMS = 5  # items in one drill: about ten minutes

# "other" has no common mistake to work on, so it never sets the focus nor fills a drill.
DRILLABLE_CATEGORIES = ("gender", "case", "verb_form", "word_order", "word_choice", "register")

CATEGORY_LABELS = {
    "gender": "le genre",
    "case": "les cas",
    "verb_form": "les formes verbales",
    "word_order": "l'ordre des mots",
    "word_choice": "le choix du mot",
    "register": "le registre",
}

DrillOutcome = Literal["matched", "not_matched"]
DrillStatus = Literal["active", "completed"]


@dataclass(frozen=True, slots=True)
class DrillItem:
    id: str  # Stable across sessions' re-reads: the same mistake keeps the same id.
    session_id: str
    category: str
    quote: str
    correction: str
    note: str  # The evaluator's explanation, without the quote and correction it was printed after.
    turns: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DrillAnswer:
    event_id: str
    item_id: str
    text: str
    outcome: DrillOutcome
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class LanguageDrill:
    id: str
    learner_id: str
    request_id: str
    focus_category: str
    items: tuple[DrillItem, ...]
    status: DrillStatus = "active"
    answers: tuple[DrillAnswer, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    version: str = DRILL_VERSION

    @property
    def matched_item_ids(self) -> frozenset[str]:
        return frozenset(a.item_id for a in self.answers if a.outcome == "matched")


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    """What the learner's recent sessions say about their language, from structured errors only."""

    sessions_considered: int
    errors_by_category: dict[str, int]  # Every categorised error of the window.
    recurring: tuple[
        str, ...
    ]  # Categories with at least RECURRING_MIN errors, most frequent first.
    focus: str | None  # The one category a drill works on, None when nothing is established.
    focus_errors: int  # Errors of the focus category in the window.
    drill_items: int  # Items a drill on the focus would hold (at most DRILL_ITEMS).


def _explanation(text: str, quote: str, correction: str) -> str:
    """The evaluation line reads `« quote » → « correction » : explanation`; keep the last part."""
    prefix = f"« {quote} » → « {correction} » : "
    return text.removeprefix(prefix)


def item_id(session_id: str, quote: str, correction: str) -> str:
    key = "\x1f".join((session_id, normalize_answer(quote), normalize_answer(correction)))
    return sha256(key.encode()).hexdigest()[:20]


def check_answer(item: DrillItem, text: str) -> DrillOutcome:
    return (
        "matched" if normalize_answer(text) == normalize_answer(item.correction) else "not_matched"
    )


def _recent(sessions: Iterable[ConversationSession]) -> list[ConversationSession]:
    completed = (
        s
        for s in sessions
        if s.status is SessionStatus.COMPLETED and not s.evaluation_account and s.evaluation
    )
    return sorted(completed, key=lambda s: s.created_at, reverse=True)[:WINDOW_SESSIONS]


def drill_candidates(sessions: Iterable[ConversationSession]) -> tuple[DrillItem, ...]:
    """The replayable errors of the recent sessions, newest first, one per distinct mistake.

    An error is replayable when the evaluation kept its quote and its correction (from
    `session-evaluation-v5`) and its category can be worked on. Two sessions that made the same
    mistake give two items: that it came back is the point.
    """
    items: list[DrillItem] = []
    seen: set[str] = set()
    for session in _recent(sessions):
        assert session.evaluation is not None
        for error in session.evaluation.language_errors:
            if not (error.quote and error.correction) or error.category not in DRILLABLE_CATEGORIES:
                continue
            identifier = item_id(session.id, error.quote, error.correction)
            if identifier in seen:
                continue
            seen.add(identifier)
            items.append(
                DrillItem(
                    id=identifier,
                    session_id=session.id,
                    category=error.category,
                    quote=error.quote,
                    correction=error.correction,
                    note=_explanation(error.text, error.quote, error.correction),
                    turns=tuple(error.evidence_turn_sequences),
                )
            )
    return tuple(items)


def language_profile(
    sessions: Iterable[ConversationSession], matched_item_ids: frozenset[str] = frozenset()
) -> LanguageProfile:
    """The language profile and the focus of the next drill.

    The focus is the most frequent category of the window that has at least RECURRING_MIN errors
    and at least one replayable item not yet matched in an earlier drill; the most recent error
    breaks a tie. Without such a category there is no focus, and no remediation is invented.
    """
    recent = _recent(sessions)
    counts: Counter[str] = Counter()
    latest: dict[str, int] = {}  # Category -> rank of the most recent session that made it.
    for rank, session in enumerate(recent):
        assert session.evaluation is not None
        for error in session.evaluation.language_errors:
            if error.category is None:
                continue
            counts[error.category] += 1
            latest.setdefault(error.category, -rank)
    replayable = [i for i in drill_candidates(recent) if i.id not in matched_item_ids]
    available = Counter(i.category for i in replayable)
    ranked = sorted(
        (c for c in DRILLABLE_CATEGORIES if counts[c] >= RECURRING_MIN and available[c]),
        key=lambda c: (-counts[c], -latest[c]),
    )
    focus = ranked[0] if ranked else None
    return LanguageProfile(
        sessions_considered=len(recent),
        errors_by_category=dict(counts),
        recurring=tuple(
            c for c, n in counts.most_common() if n >= RECURRING_MIN and c in DRILLABLE_CATEGORIES
        ),
        focus=focus,
        focus_errors=counts[focus] if focus else 0,
        drill_items=min(DRILL_ITEMS, available[focus]) if focus else 0,
    )


def select_items(
    sessions: Iterable[ConversationSession],
    focus: str,
    matched_item_ids: frozenset[str] = frozenset(),
) -> tuple[DrillItem, ...]:
    """The items of a drill on `focus`: newest first, none that an earlier drill already matched."""
    return tuple(
        i
        for i in drill_candidates(sessions)
        if i.category == focus and i.id not in matched_item_ids
    )[:DRILL_ITEMS]
