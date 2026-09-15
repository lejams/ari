"""OpenAI Realtime speech-to-speech relay. PCM16 24 kHz in both directions."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import AsyncIterator
from typing import Any, cast

from openai import AsyncOpenAI
from openai.resources.realtime.realtime import AsyncRealtimeConnection

from ari.application.contracts import ExecutionContext
from ari.application.ports.realtime import RealtimeEvent
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id

OPERATION = "speech_to_speech"


def _record(
    context: ExecutionContext,
    model: str,
    status: ExecutionStatus,
    latency_ms: int,
    usage: dict[str, object],
    *,
    response_id: str | None = None,
    error: Exception | str | None = None,
) -> ExecutionRecord:
    return ExecutionRecord(
        id=new_id(),
        session_id=context.session_id,
        turn_id=context.turn_id,
        operation=OPERATION,
        provider="openai",
        model=model,
        status=status,
        prompt_version=context.prompt_version,
        prompt_hash=context.prompt_hash,
        case_version=context.case_version,
        case_hash=context.case_hash,
        latency_ms=latency_ms,
        usage=usage,
        provider_request_id=response_id,
        error_code=(type(error).__name__ if isinstance(error, Exception) else error),
        error_message=str(error)[:1000] if error else None,
        retryable=error is not None,
    )


class RealtimeEventTranslator:
    """Turns vendor server events into `RealtimeEvent`s and times each patient response."""

    def __init__(self, context: ExecutionContext, model: str) -> None:
        self._context = context
        self._model = model
        self._response_started: dict[str, float] = {}

    def translate(self, event: Any) -> RealtimeEvent | None:
        kind = str(event.type)
        if kind == "input_audio_buffer.speech_started":
            return RealtimeEvent("speech_started")
        if kind == "conversation.item.input_audio_transcription.completed":
            return RealtimeEvent("user_transcript", item_id=event.item_id, text=event.transcript)
        if kind == "conversation.item.input_audio_transcription.failed":
            return RealtimeEvent("user_transcript", item_id=event.item_id, text="")
        if kind == "response.created":
            self._response_started[str(event.response.id)] = time.perf_counter()
            return None
        if kind == "response.output_audio.delta":
            return RealtimeEvent(
                "patient_audio", response_id=event.response_id, audio=base64.b64decode(event.delta)
            )
        if kind == "response.output_audio_transcript.done":
            return RealtimeEvent(
                "patient_transcript", response_id=event.response_id, text=event.transcript
            )
        if kind == "response.done":
            response = event.response
            response_id = str(response.id)
            started = self._response_started.pop(response_id, time.perf_counter())
            status = str(response.status or "completed")
            usage: dict[str, object] = {"response_status": status}
            if response.usage is not None:
                usage.update(response.usage.model_dump(exclude_none=True))
            failed = status == "failed"
            return RealtimeEvent(
                "response_completed",
                response_id=response_id,
                completed=status == "completed",
                execution=_record(
                    self._context,
                    self._model,
                    ExecutionStatus.FAILED if failed else ExecutionStatus.SUCCEEDED,
                    int((time.perf_counter() - started) * 1000),
                    usage,
                    response_id=response_id,
                    error=f"response_{status}" if failed else None,
                ),
            )
        if kind == "error":
            return RealtimeEvent("error", message=str(getattr(event.error, "message", event.error)))
        return None


class OpenAIRealtimeConnection:
    def __init__(
        self, connection: AsyncRealtimeConnection, context: ExecutionContext, model: str
    ) -> None:
        self._connection = connection
        self._translator = RealtimeEventTranslator(context, model)

    async def send_audio(self, pcm16: bytes) -> None:
        await self._connection.input_audio_buffer.append(
            audio=base64.b64encode(pcm16).decode("ascii")
        )

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        async for raw in self._connection:
            event = self._translator.translate(raw)
            if event is not None:
                yield event

    async def close(self) -> None:
        await self._connection.close()


class OpenAIRealtimeEngine:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        voice: str,
        transcription_model: str,
        vad_silence_ms: int,
        connect_timeout_seconds: float,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._voice = voice
        self._transcription_model = transcription_model
        self._vad_silence_ms = vad_silence_ms
        self._connect_timeout_seconds = connect_timeout_seconds

    def session_config(self, instructions: str, language: str) -> dict[str, Any]:
        return {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {"model": self._transcription_model, "language": language},
                    "turn_detection": {
                        "type": "server_vad",
                        "silence_duration_ms": self._vad_silence_ms,
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": self._voice},
            },
        }

    async def open(
        self, *, instructions: str, language: str, context: ExecutionContext
    ) -> OpenAIRealtimeConnection:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._connect_timeout_seconds):
                connection = await self._client.realtime.connect(model=self._model).enter()
                await connection.session.update(
                    session=cast(Any, self.session_config(instructions, language))
                )
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            execution = _record(
                context, self._model, ExecutionStatus.FAILED, latency, {}, error=exc
            )
            raise ProviderError("OpenAI Realtime connection failed", execution=execution) from exc
        return OpenAIRealtimeConnection(connection, context, self._model)
