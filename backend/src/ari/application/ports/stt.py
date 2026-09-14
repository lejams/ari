from dataclasses import dataclass
from typing import Protocol

from ari.application.contracts import ExecutionContext, TranscriptionConfig
from ari.domain.models import ExecutionRecord


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    execution: ExecutionRecord


class UtteranceTranscriber(Protocol):
    """Transcribes one complete learner utterance (PCM16 mono) after the turn ends."""

    async def transcribe(
        self,
        pcm16: bytes,
        *,
        sample_rate: int,
        context: ExecutionContext,
        config: TranscriptionConfig,
    ) -> Transcription: ...
