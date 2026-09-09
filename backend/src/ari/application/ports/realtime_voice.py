from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from ari.application.contracts import ExecutionContext
from ari.domain.models import ExecutionRecord, InteractionMode, VoiceProfile


class RealtimeResponseKind(StrEnum):
    OPENING = "opening"
    PATIENT_ANSWER = "patient_answer"


@dataclass(frozen=True, slots=True)
class RealtimeSimulationSpec:
    instructions: str
    language: str
    transcription_context: str
    prompt_version: str
    prompt_hash: str


@dataclass(frozen=True, slots=True)
class RealtimeProviderEvent:
    type: str
    text: str | None = None
    input_item_id: str | None = None
    response_id: str | None = None
    audio_end_ms: int | None = None
    response_status: str | None = None
    usage: dict[str, Any] | None = None
    execution: ExecutionRecord | None = None
    response_kind: RealtimeResponseKind = RealtimeResponseKind.PATIENT_ANSWER
    raw: dict[str, Any] | None = None


class RealtimeCall(Protocol):
    call_id: str
    provider: str
    model: str
    answer_sdp: str
    execution: ExecutionRecord

    def events(self) -> AsyncIterator[RealtimeProviderEvent]: ...

    async def update_simulation(self, simulation: RealtimeSimulationSpec) -> None: ...

    async def begin_user_turn(self) -> None: ...

    async def commit_user_turn(self) -> None: ...

    async def interrupt_response(self) -> None: ...

    async def create_response(
        self,
        *,
        kind: RealtimeResponseKind = RealtimeResponseKind.PATIENT_ANSWER,
        exact_text: str | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


class RealtimeVoiceEngine(Protocol):
    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> RealtimeCall: ...
