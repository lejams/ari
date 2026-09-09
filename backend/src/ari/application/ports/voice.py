from collections.abc import AsyncIterator
from typing import Protocol

from ari.application.contracts import AudioStreamEvent, ExecutionContext, TranscriptionConfig
from ari.application.ports.stt import StreamingSTTConnection


class VoiceEngine(Protocol):
    async def open_transcription(
        self, context: ExecutionContext, config: TranscriptionConfig
    ) -> StreamingSTTConnection: ...

    def stream_response(
        self, text: str, context: ExecutionContext
    ) -> AsyncIterator[AudioStreamEvent]: ...
