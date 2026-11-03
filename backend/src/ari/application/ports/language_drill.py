from typing import Protocol

from ari.domain.language_drill import DrillAnswer, LanguageDrill


class LanguageDrillRepository(Protocol):
    def get(self, drill_id: str, learner_id: str) -> LanguageDrill: ...

    def find_request(self, learner_id: str, request_id: str) -> LanguageDrill | None: ...

    def list(self, learner_id: str) -> tuple[LanguageDrill, ...]: ...

    def create(self, drill: LanguageDrill) -> LanguageDrill:
        """Stores a new drill; returns the existing one when the request, or an unfinished drill of
        the same learner, already won (one unfinished drill per learner)."""
        ...

    def append_answer(
        self, drill_id: str, learner_id: str, answer: DrillAnswer
    ) -> LanguageDrill: ...

    def finish(self, drill_id: str, learner_id: str) -> LanguageDrill: ...
