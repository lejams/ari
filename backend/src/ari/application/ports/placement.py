from typing import Any, Protocol

from ari.domain.placement import PlacementAttempt, PlacementSetVersion


class PlacementCatalog(Protocol):
    """Published placement sets, read from the registry."""

    def published(self, language: str | None = None) -> PlacementSetVersion | None: ...

    def get(self, set_id: str, version: str) -> tuple[PlacementSetVersion, str]: ...


class PlacementAttemptRepository(Protocol):
    def get(self, attempt_id: str, learner_id: str) -> PlacementAttempt: ...

    def find_request(self, learner_id: str, request_id: str) -> PlacementAttempt | None: ...

    def list(self, learner_id: str) -> tuple[PlacementAttempt, ...]: ...

    def create(self, attempt: PlacementAttempt) -> PlacementAttempt: ...

    def append_answer(
        self,
        attempt_id: str,
        learner_id: str,
        *,
        event_id: str,
        item_id: str,
        phase: str,
        payload: dict[str, Any],
        state: dict[str, Any],
        next_phase: str,
    ) -> PlacementAttempt: ...

    def finish(
        self, attempt_id: str, learner_id: str, result: dict[str, Any]
    ) -> PlacementAttempt: ...
