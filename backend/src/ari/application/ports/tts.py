from collections.abc import AsyncIterator
from typing import Protocol

from ari.application.contracts import AudioStreamEvent, ExecutionContext


class StreamingTTSProvider(Protocol):
    def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]: ...
