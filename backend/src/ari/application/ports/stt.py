from collections.abc import AsyncIterator
from typing import Protocol

from ari.application.contracts import ExecutionContext, STTEvent, TranscriptionConfig


class StreamingSTTConnection(Protocol):
    async def send_audio(self, pcm16: bytes) -> None: ...

    def events(self) -> AsyncIterator[STTEvent]: ...

    async def close(self) -> None: ...


class StreamingSTTProvider(Protocol):
    async def connect(
        self, context: ExecutionContext, config: TranscriptionConfig
    ) -> StreamingSTTConnection: ...
