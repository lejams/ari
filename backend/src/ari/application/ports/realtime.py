"""Speech-to-speech port: the model hears the learner continuously and answers in audio.

Used by the exam mode (open microphone). The application never sees vendor events, only
the normalised `RealtimeEvent` stream below.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from ari.application.contracts import ExecutionContext
from ari.domain.models import ExecutionRecord

RealtimeEventType = Literal[
    "speech_started",  # the learner started talking (barge-in if the patient was speaking)
    "user_transcript",  # final transcript of one learner utterance (`item_id`, `text`)
    "patient_audio",  # one PCM16 24 kHz chunk of the patient answer (`response_id`, `audio`)
    "patient_transcript",  # full transcript of the patient answer (`response_id`, `text`)
    "response_completed",  # the patient answer ended (`response_id`, `completed`, `execution`)
    "error",  # non-fatal provider notice (`message`)
]


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    type: RealtimeEventType
    item_id: str | None = None
    response_id: str | None = None
    text: str = ""
    audio: bytes = b""
    completed: bool = True
    execution: ExecutionRecord | None = None
    message: str = ""


class RealtimeConnection(Protocol):
    async def send_audio(self, pcm16: bytes) -> None: ...

    def events(self) -> AsyncIterator[RealtimeEvent]: ...

    async def close(self) -> None: ...


class RealtimeVoiceEngine(Protocol):
    """Opens one bidirectional voice conversation briefed with the case instructions."""

    async def open(
        self, *, instructions: str, language: str, context: ExecutionContext
    ) -> RealtimeConnection:
        """`language` is the ISO-639-1 code of the simulation language, e.g. `de`."""
        ...
