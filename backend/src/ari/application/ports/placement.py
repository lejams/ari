from dataclasses import dataclass
from typing import Any, Protocol

from ari.domain.placement import PlacementAttempt, PlacementSetVersion


class PlacementCatalog(Protocol):
    """Published placement sets, read from the registry."""

    def published(self, language: str | None = None) -> PlacementSetVersion | None: ...

    def get(self, set_id: str, version: str) -> tuple[PlacementSetVersion, str]: ...

    def record_item_report(
        self,
        set_id: str,
        version: str,
        *,
        item_id: str,
        learner_id: str,
        attempt_id: str,
        note: str,
    ) -> None: ...


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


@dataclass(frozen=True, slots=True)
class StoredAudio:
    """The recorded audio of a listening item: PCM16 mono, with where it comes from."""

    pcm16: bytes
    sample_rate: int
    voice: str  # "<provider>:<voice id>"
    model: str


class PlacementAudioStore(Protocol):
    """Audio recorded once per set hash and item; the latest voice of an item is the one played."""

    def get(self, set_hash: str, item_id: str) -> StoredAudio | None: ...

    def has(self, set_hash: str, item_id: str, voice: str) -> bool: ...

    def put(
        self, set_id: str, version: str, set_hash: str, item_id: str, audio: StoredAudio
    ) -> bool:
        """Write once; False (nothing written) when this voice already has this item."""
        ...
