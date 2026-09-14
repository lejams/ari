from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai.types.realtime import RealtimeTranscriptionSessionCreateRequestParam
from websockets.asyncio.client import ClientConnection, connect

from ari.application.contracts import ExecutionContext, STTEvent, TranscriptionConfig
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id


class RealtimeHandshakeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OpenAIStreamingSTTConnection:
    def __init__(
        self,
        socket: ClientConnection,
        context: ExecutionContext,
        config: TranscriptionConfig,
        model: str,
        sample_rate: int,
    ) -> None:
        self._socket = socket
        self._context = context
        self._config = config
        self._model = model
        self._sample_rate = sample_rate
        self._pending_bytes = 0
        self._active_item_id: str | None = None
        self._bytes_by_item: dict[str, int] = {}
        self._audio_start_ms_by_item: dict[str, int] = {}
        self._audio_end_ms_by_item: dict[str, int] = {}
        self._audio_duration_ms_by_item: dict[str, int] = {}
        self._speech_stopped_at_by_item: dict[str, float] = {}
        self._transcript_final_at_by_item: dict[str, float] = {}
        self._started_by_item: dict[str, float] = {}
        self._item_order: list[str] = []
        self._completed: dict[str, dict[str, Any]] = {}

    async def send_audio(self, pcm16: bytes) -> None:
        if self._active_item_id is None:
            self._pending_bytes += len(pcm16)
        else:
            self._bytes_by_item[self._active_item_id] = self._bytes_by_item.get(
                self._active_item_id, 0
            ) + len(pcm16)
        await self._socket.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm16).decode("ascii"),
                }
            )
        )

    async def events(self) -> AsyncIterator[STTEvent]:
        async for raw_message in self._socket:
            payload: dict[str, Any] = json.loads(raw_message)
            event_type = str(payload.get("type", ""))
            if event_type == "input_audio_buffer.speech_started":
                item_id = str(payload.get("item_id", ""))
                if item_id:
                    self._active_item_id = item_id
                    self._item_order.append(item_id)
                    self._started_by_item[item_id] = time.perf_counter()
                    audio_start_ms = payload.get("audio_start_ms")
                    if isinstance(audio_start_ms, int):
                        self._audio_start_ms_by_item[item_id] = audio_start_ms
                    self._bytes_by_item[item_id] = self._pending_bytes
                    self._pending_bytes = 0
                yield STTEvent(type="speech_started", item_id=item_id or None, raw=payload)
            elif event_type == "input_audio_buffer.speech_stopped":
                item_id = str(payload.get("item_id", ""))
                audio_end_ms = payload.get("audio_end_ms")
                audio_start_ms = self._audio_start_ms_by_item.get(item_id)
                if isinstance(audio_end_ms, int) and audio_start_ms is not None:
                    self._audio_duration_ms_by_item[item_id] = max(0, audio_end_ms - audio_start_ms)
                if isinstance(audio_end_ms, int) and item_id:
                    self._audio_end_ms_by_item[item_id] = audio_end_ms
                if item_id:
                    self._speech_stopped_at_by_item[item_id] = time.perf_counter()
                if self._active_item_id == item_id:
                    self._active_item_id = None
                yield STTEvent(type="speech_stopped", item_id=item_id or None, raw=payload)
            elif event_type == "conversation.item.input_audio_transcription.delta":
                yield STTEvent(
                    type="transcript_delta",
                    text=str(payload.get("delta", "")),
                    item_id=payload.get("item_id"),
                    raw=payload,
                )
            elif event_type == "conversation.item.input_audio_transcription.completed":
                item_id = str(payload.get("item_id", ""))
                if item_id:
                    self._transcript_final_at_by_item[item_id] = time.perf_counter()
                if item_id and item_id not in self._item_order:
                    self._item_order.append(item_id)
                    self._started_by_item[item_id] = time.perf_counter()
                    self._bytes_by_item[item_id] = self._pending_bytes
                    self._pending_bytes = 0
                if item_id:
                    self._completed[item_id] = payload
                    if self._active_item_id == item_id:
                        self._active_item_id = None
                while self._item_order and self._item_order[0] in self._completed:
                    ready_id = self._item_order.pop(0)
                    ready = self._completed.pop(ready_id)
                    yield STTEvent(
                        type="transcript_final",
                        text=str(ready.get("transcript", "")),
                        item_id=ready_id,
                        execution=self._execution(ready_id),
                        raw=ready,
                    )
            elif event_type == "error":
                yield STTEvent(type="error", execution=self._error_execution(payload), raw=payload)

    async def close(self) -> None:
        await self._socket.close()

    def _execution(self, item_id: str) -> ExecutionRecord:
        audio_bytes = self._bytes_by_item.pop(item_id, 0)
        duration_ms = self._audio_duration_ms_by_item.pop(item_id, None)
        audio_start_ms = self._audio_start_ms_by_item.pop(item_id, None)
        audio_end_ms = self._audio_end_ms_by_item.pop(item_id, None)
        # Pending PCM includes unsegmented silence; it cannot price an individual turn.
        seconds = duration_ms / 1000 if duration_ms is not None else None
        started = self._started_by_item.pop(item_id, time.perf_counter())
        speech_stopped = self._speech_stopped_at_by_item.pop(item_id, None)
        transcript_final = self._transcript_final_at_by_item.pop(item_id, None)
        endpoint_latency_ms = (
            int((transcript_final - speech_stopped) * 1000)
            if speech_stopped is not None and transcript_final is not None
            else None
        )
        usage = {
            "audio_seconds": round(seconds, 3) if seconds is not None else None,
            "audio_duration_source": "provider_vad" if duration_ms is not None else "unknown",
            "observed_pcm_bytes": audio_bytes,
            "speech_end_to_transcript_final_ms": endpoint_latency_ms,
            "provider_speech_start_ms": audio_start_ms,
            "provider_speech_end_ms": audio_end_ms,
        }
        return ExecutionRecord(
            id=new_id(),
            session_id=self._context.session_id,
            turn_id=self._context.turn_id,
            operation="speech_to_text",
            provider="openai",
            model=self._model,
            status=ExecutionStatus.SUCCEEDED,
            prompt_version=self._config.prompt_version,
            prompt_hash=self._config.prompt_hash,
            case_version=self._context.case_version,
            case_hash=self._context.case_hash,
            latency_ms=(
                endpoint_latency_ms
                if endpoint_latency_ms is not None
                else int((time.perf_counter() - started) * 1000)
            ),
            usage=usage,
            provider_request_id=item_id,
        )

    def _error_execution(self, payload: dict[str, Any]) -> ExecutionRecord:
        error = payload.get("error")
        details = error if isinstance(error, dict) else {}
        return ExecutionRecord(
            id=new_id(),
            session_id=self._context.session_id,
            turn_id=self._context.turn_id,
            operation="speech_to_text",
            provider="openai",
            model=self._model,
            status=ExecutionStatus.FAILED,
            prompt_version=self._config.prompt_version,
            prompt_hash=self._config.prompt_hash,
            case_version=self._context.case_version,
            case_hash=self._context.case_hash,
            latency_ms=0,
            usage={
                "sent_pcm_audio_seconds": round(
                    (sum(self._bytes_by_item.values()) + self._pending_bytes)
                    / (self._sample_rate * 2),
                    3,
                )
            },
            error_code=str(details.get("code", "realtime_error")),
            error_message=str(details.get("message", "Realtime transcription failed"))[:1000],
            retryable=True,
        )


class OpenAIStreamingSTTProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        realtime_url: str,
        sample_rate: int,
        silence_ms: int,
        prefix_padding_ms: int,
        vad_threshold: float,
        handshake_timeout_seconds: float,
    ) -> None:
        if sample_rate != 24_000:
            raise ValueError("OpenAI Realtime PCM transcription requires a 24000 Hz sample rate")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._realtime_url = realtime_url
        self._sample_rate = sample_rate
        self._silence_ms = silence_ms
        self._prefix_padding_ms = prefix_padding_ms
        self._vad_threshold = vad_threshold
        self._handshake_timeout_seconds = handshake_timeout_seconds

    async def connect(
        self, context: ExecutionContext, config: TranscriptionConfig
    ) -> OpenAIStreamingSTTConnection:
        started = time.perf_counter()
        socket: ClientConnection | None = None
        try:
            language = self._primary_language(config.expected_locales[0])
            session: RealtimeTranscriptionSessionCreateRequestParam = {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24_000},
                        "transcription": {
                            "model": self._model,
                            "language": language,
                            "prompt": config.context_prompt,
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": self._vad_threshold,
                            "prefix_padding_ms": self._prefix_padding_ms,
                            "silence_duration_ms": self._silence_ms,
                        },
                        "noise_reduction": {"type": "near_field"},
                    }
                },
            }
            async with asyncio.timeout(self._handshake_timeout_seconds):
                client_secret = await self._create_client_secret(session)
            socket = await connect(
                self._realtime_url,
                additional_headers={"Authorization": f"Bearer {client_secret}"},
                open_timeout=self._handshake_timeout_seconds,
            )
            async with asyncio.timeout(self._handshake_timeout_seconds):
                while True:
                    payload: dict[str, Any] = json.loads(await socket.recv())
                    event_type = str(payload.get("type", ""))
                    if event_type == "session.created":
                        break
                    if event_type == "error":
                        error = payload.get("error")
                        details = error if isinstance(error, dict) else {}
                        raise RealtimeHandshakeError(
                            str(details.get("code", "realtime_session_error")),
                            str(details.get("message", "Realtime session failed")),
                        )
            return OpenAIStreamingSTTConnection(
                socket, context, config, self._model, self._sample_rate
            )
        except Exception as exc:
            if socket is not None:
                await socket.close()
            error_code, request_id, retryable = self._error_details(exc)
            execution = ExecutionRecord(
                id=new_id(),
                session_id=context.session_id,
                operation="speech_to_text.connect",
                provider="openai",
                model=self._model,
                status=ExecutionStatus.FAILED,
                prompt_version=config.prompt_version,
                prompt_hash=config.prompt_hash,
                case_version=context.case_version,
                case_hash=context.case_hash,
                latency_ms=int((time.perf_counter() - started) * 1000),
                usage={},
                provider_request_id=request_id,
                error_code=error_code,
                error_message=str(exc)[:1000],
                retryable=retryable,
            )
            raise ProviderError(
                "OpenAI transcription connection failed", execution=execution
            ) from exc

    async def _create_client_secret(
        self, session: RealtimeTranscriptionSessionCreateRequestParam
    ) -> str:
        response = await self._client.realtime.client_secrets.create(session=session)
        return response.value

    @staticmethod
    def _error_details(exc: Exception) -> tuple[str, str | None, bool]:
        if isinstance(exc, APIStatusError):
            retryable = exc.status_code in {408, 409, 429} or exc.status_code >= 500
            return exc.code or f"http_{exc.status_code}", exc.request_id, retryable
        if isinstance(exc, APIConnectionError):
            return exc.code or type(exc).__name__, None, True
        if isinstance(exc, RealtimeHandshakeError):
            return exc.code, None, exc.code in {"rate_limit_exceeded", "server_error"}
        return type(exc).__name__, None, True

    @staticmethod
    def _primary_language(locale: str) -> str:
        primary = locale.strip().replace("_", "-").split("-", maxsplit=1)[0].lower()
        if len(primary) not in {2, 3} or not primary.isalpha():
            raise ValueError(f"Unsupported transcription locale: {locale}")
        return primary
