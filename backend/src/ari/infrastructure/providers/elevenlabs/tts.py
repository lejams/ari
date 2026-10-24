"""ElevenLabs speech synthesis over the Text to Dialogue WebSocket (`eleven_v4_turbo`).

`eleven_v4_turbo` is only served by the Text to Dialogue WebSocket, one voice per connection
(docs/spikes/exam-elevenlabs.md, section 2). The protocol below follows ElevenLabs'
documentation as read on 19 October 2026, corrected by the first live call (24 October 2026):
the service validates `voices` as a list of voice id strings and refuses objects (code 1007), so
voices are registered by id and voice settings go beside them in the same first message.
`voice_registration="objects"` stays for comparison only. Final frames are tolerated under every
documented name (`isFinal`, `is_final`, `is_final_audio_for_turn`, `final`), binary frames too.

One sentence at a time per connection: a sentence is sent with `flush`, its audio frames are
relayed as they arrive, and the sentence ends on a final frame, or after a short silence once
audio has started. Connections are pooled and reused while fresh; a connection that ended a
sentence any other way (silence, interruption, error) is closed, since frames of an abandoned
sentence could otherwise leak into the next one. A connection that turns out to be closed
before any audio is replaced once; after audio has been relayed nothing is retried.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Literal, Protocol
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id

DEFAULT_URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
FINAL_KEYS = ("isFinal", "is_final", "is_final_audio_for_turn", "final")
VoiceRegistration = Literal["objects", "ids"]


class DialogueSocket(Protocol):
    """The part of a WebSocket client connection this adapter uses."""

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


Connector = Callable[[str, dict[str, str], float], Awaitable[DialogueSocket]]


async def _websocket_connect(url: str, headers: dict[str, str], timeout: float) -> DialogueSocket:
    return await connect(url, additional_headers=headers, max_size=None, open_timeout=timeout)


class ElevenLabsProtocolError(RuntimeError):
    """The service answered something this adapter cannot use."""


class _Pooled:
    __slots__ = ("idle_since", "socket")

    def __init__(self, socket: DialogueSocket) -> None:
        self.socket = socket
        self.idle_since = time.monotonic()


# The training player accepts only audio/pcm;rate=24000 (web/app.js) and the placement oral
# wraps the bytes at ARI_AUDIO_SAMPLE_RATE (24 kHz): any other rate would play at the wrong speed.
SUPPORTED_OUTPUT_FORMATS = frozenset({"pcm_24000"})


def pcm_rate(output_format: str) -> int:
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"ElevenLabs output format {output_format!r}: the voice pipeline plays raw PCM at "
            f"24 kHz only, use {', '.join(sorted(SUPPORTED_OUTPUT_FORMATS))}"
        )
    return int(output_format.partition("_")[2])


class ElevenLabsStreamingTTSProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        voice_id: str,
        timeout_seconds: float,
        output_format: str = "pcm_24000",
        base_url: str = DEFAULT_URL,
        voice_registration: VoiceRegistration = "ids",
        idle_end_seconds: float = 1.5,
        max_idle_connections: int = 4,
        max_connection_idle_seconds: float = 15.0,
        connector: Connector = _websocket_connect,
        voice_settings: Mapping[str, float] | None = None,
        audio_tags: str = "",
    ) -> None:
        if not api_key or not voice_id:
            raise ValueError("ElevenLabs speech synthesis needs an API key and a voice id")
        self._api_key = api_key
        self._model = model
        self._voice_id = voice_id
        self._timeout_seconds = timeout_seconds
        self._output_format = output_format
        self._rate = pcm_rate(output_format)
        self._mime_type = f"audio/pcm;rate={self._rate}"
        self._url = f"{base_url}?{urlencode({'model_id': model, 'output_format': output_format})}"
        self._voice_registration = voice_registration
        self._idle_end_seconds = idle_end_seconds
        self._max_idle_connections = max_idle_connections
        # The service closes a connection left silent for about 20 s (keep_alive every 10 s
        # holds it open); rather than pinging, a connection idle longer than this is dropped.
        self._max_connection_idle_seconds = max_connection_idle_seconds
        self._connector = connector
        self._idle: list[_Pooled] = []
        # Persona levers beyond the voice itself (exam). Settings go with the voice when a
        # connection registers it; tags (bracketed, never words) are read before each sentence.
        # Neither has been run against the live service yet.
        self._voice_settings = dict(voice_settings or {})
        self._audio_tags = audio_tags.strip()

    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        started = time.perf_counter()
        # One deadline for the whole sentence (connection, retry and every frame), like the
        # enclosing timeout of the OpenAI adapter: a service that keeps streaming cannot hold a
        # turn past ARI_TTS_TIMEOUT_SECONDS.
        deadline = time.monotonic() + self._timeout_seconds
        first_audio_ms: int | None = None
        audio_bytes = 0
        reusable = False
        socket: DialogueSocket | None = None
        spoken = f"{self._audio_tags} {text}" if self._audio_tags else text
        try:
            for attempt in (1, 2):
                socket = await self._acquire(deadline)
                message = {
                    "inputs": [{"text": spoken, "voice_id": self._voice_id}],
                    "flush": True,
                }
                try:
                    await socket.send(json.dumps(message))
                    async for chunk, final in self._sentence(socket, deadline):
                        if final:
                            reusable = True
                            break
                        if first_audio_ms is None:
                            first_audio_ms = int((time.perf_counter() - started) * 1000)
                        audio_bytes += len(chunk)
                        yield AudioStreamEvent("chunk", data=chunk, mime_type=self._mime_type)
                    break
                except TimeoutError:
                    raise  # an OSError too, but a slow service is not retried
                except (ConnectionClosed, OSError):
                    await _close_quietly(socket)
                    socket = None
                    if attempt == 2 or audio_bytes:
                        raise
            if not audio_bytes:
                raise ElevenLabsProtocolError("the service ended the sentence without audio")
            execution = self._record(
                context,
                started,
                ExecutionStatus.SUCCEEDED,
                len(spoken),
                first_audio_ms=first_audio_ms,
                audio_seconds=round(audio_bytes / (self._rate * 2), 3),
            )
            yield AudioStreamEvent("completed", mime_type=self._mime_type, execution=execution)
        except Exception as exc:
            reusable = False
            execution = self._record(context, started, ExecutionStatus.FAILED, len(spoken), exc)
            raise ProviderError("ElevenLabs speech synthesis failed", execution=execution) from exc
        finally:
            # Also reached when the caller stops listening (barge-in): the sentence is abandoned
            # mid-stream and there is no cancel message, so that connection is not reused.
            if socket is not None:
                if reusable:
                    await self._release(socket)
                else:
                    await _close_quietly(socket)

    async def close(self) -> None:
        idle, self._idle = self._idle, []
        for pooled in idle:
            await _close_quietly(pooled.socket)

    async def _sentence(
        self, socket: DialogueSocket, deadline: float
    ) -> AsyncIterator[tuple[bytes, bool]]:
        """(audio, False) per audio frame, then (b"", True) on the final frame."""
        heard = False
        while True:
            remaining = _remaining(deadline)
            idle_end = heard and self._idle_end_seconds < remaining
            try:
                raw = await asyncio.wait_for(
                    socket.recv(), self._idle_end_seconds if idle_end else remaining
                )
            except TimeoutError:
                if idle_end:
                    return  # silence after audio: the sentence is over, but not cleanly
                raise TimeoutError(f"speech synthesis exceeded {self._timeout_seconds} s") from None
            frame = _frame(raw)
            if frame.get("error"):
                raise ElevenLabsProtocolError(f"service error: {_redact(frame)}")
            audio = frame.get("audio")
            if audio:
                heard = True
                yield base64.b64decode(audio), False
            if any(frame.get(key) for key in FINAL_KEYS):
                yield b"", True
                return

    async def _acquire(self, deadline: float) -> DialogueSocket:
        now = time.monotonic()
        while self._idle:
            pooled = self._idle.pop()
            if now - pooled.idle_since <= self._max_connection_idle_seconds:
                return pooled.socket
            await _close_quietly(pooled.socket)
        return await self._open(_remaining(deadline))

    async def _release(self, socket: DialogueSocket) -> None:
        if len(self._idle) < self._max_idle_connections:
            self._idle.append(_Pooled(socket))
        else:
            await _close_quietly(socket)

    async def _open(self, timeout_seconds: float) -> DialogueSocket:
        try:
            socket = await self._connector(
                self._url, {"xi-api-key": self._api_key}, timeout_seconds
            )
        except InvalidStatus as exc:
            # The body says why (bad key, unknown model, format); the key itself is never in it.
            body = exc.response.body.decode("utf-8", "replace")[:300] if exc.response.body else ""
            raise ElevenLabsProtocolError(
                f"connection refused (HTTP {exc.response.status_code}): {body}"
            ) from exc
        first: dict[str, Any]
        if self._voice_registration == "ids":
            # Settings are only accepted on this first message, beside the voices.
            first = {"voices": [self._voice_id]}
            if self._voice_settings:
                first["voice_settings"] = self._voice_settings
        else:
            voice: dict[str, Any] = {"voice_id": self._voice_id}
            if self._voice_settings:
                voice["voice_settings"] = self._voice_settings
            first = {"voices": [voice]}
        await socket.send(json.dumps(first))
        return socket

    def _record(
        self,
        context: ExecutionContext,
        started: float,
        status: ExecutionStatus,
        characters: int,
        error: Exception | None = None,
        first_audio_ms: int | None = None,
        audio_seconds: float | None = None,
    ) -> ExecutionRecord:
        usage: dict[str, object] = {
            "characters": characters,
            "first_audio_ms": first_audio_ms,
            "output_format": self._output_format,
        }
        if audio_seconds is not None:
            usage["audio_seconds"] = audio_seconds
        return ExecutionRecord(
            id=new_id(),
            session_id=context.session_id,
            turn_id=context.turn_id,
            operation="text_to_speech",
            provider="elevenlabs",
            model=self._model,
            status=status,
            prompt_version=None,
            prompt_hash=None,
            case_version=context.case_version,
            case_hash=context.case_hash,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
            error_code=type(error).__name__ if error else None,
            error_message=str(error)[:1000] if error else None,
            retryable=error is not None,
        )


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("speech synthesis deadline passed")
    return remaining


def _frame(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        return {"audio": base64.b64encode(raw).decode("ascii")}
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ElevenLabsProtocolError("unreadable frame from the service") from exc
    if not isinstance(frame, dict):
        raise ElevenLabsProtocolError("unexpected frame shape from the service")
    return frame


def _redact(frame: dict[str, Any]) -> dict[str, Any]:
    return {key: ("<audio>" if key == "audio" else value) for key, value in frame.items()}


async def _close_quietly(socket: DialogueSocket) -> None:
    # Closing a broken connection may fail in any way; there is nothing left to save.
    with contextlib.suppress(Exception):
        await socket.close()
