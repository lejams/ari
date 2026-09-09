from collections.abc import AsyncIterator

from ari.application.contracts import AudioStreamEvent, ExecutionContext, TranscriptionConfig
from ari.application.ports.stt import StreamingSTTConnection, StreamingSTTProvider
from ari.application.ports.tts import StreamingTTSProvider


class TurnBasedVoiceEngine:
    def __init__(
        self,
        stt: StreamingSTTProvider,
        tts: StreamingTTSProvider,
    ) -> None:
        self._stt = stt
        self._tts = tts

    async def open_transcription(
        self, context: ExecutionContext, config: TranscriptionConfig
    ) -> StreamingSTTConnection:
        return await self._stt.connect(context, config)

    def stream_response(
        self, text: str, context: ExecutionContext
    ) -> AsyncIterator[AudioStreamEvent]:
        return self._tts.stream(text, context)
