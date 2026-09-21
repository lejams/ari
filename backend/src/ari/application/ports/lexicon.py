from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ari.domain.models import LexiconEntry, LexiconReview


@dataclass(frozen=True, slots=True)
class LexiconIngestReport:
    """What one analysed session changed in the learner's lexicon."""

    session_id: str
    added: tuple[LexiconEntry, ...]
    promoted: tuple[LexiconEntry, ...]
    wrong_language_turns: tuple[int, ...]
    srs_version: str


class LexiconRepository(Protocol):
    def get(self, learner_id: str, entry_id: str) -> LexiconEntry: ...

    def list(
        self, learner_id: str, *, include_archived: bool = False
    ) -> tuple[LexiconEntry, ...]: ...

    def upsert(self, entries: Iterable[LexiconEntry]) -> None: ...

    def find_review(self, entry_id: str, event_id: str) -> LexiconReview | None: ...

    def record_review(self, review: LexiconReview, entry: LexiconEntry) -> LexiconEntry: ...

    def save_report(self, report: LexiconIngestReport) -> None: ...

    def get_report(self, session_id: str) -> LexiconIngestReport | None: ...
