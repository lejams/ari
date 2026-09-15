"""One WebSocket per active voice session: PCM in, transcript, patient text, PCM out.

Connection state lives in this process only, so the API must run as a single worker.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import cast

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ari.api.voice_session_dto import public_turn
from ari.application.contracts import ExecutionContext, TranscriptionConfig
from ari.application.schemas import ClientControlMessage
from ari.container import Container
from ari.domain.errors import AriError, ProviderError
from ari.domain.models import (
    ConversationSession,
    ConversationTurn,
    ExecutionRecord,
    ExecutionStatus,
    MedicalCase,
    SessionStatus,
    TurnResponseState,
    new_id,
)

PCM_MIME_TYPE = "audio/pcm;rate=24000"
MAX_TURN_SECONDS = 300
MIN_TURN_SECONDS = 0.25


def is_websocket_disconnect_runtime(exc: RuntimeError) -> bool:
    return "disconnect message has been received" in str(exc)


@dataclass(slots=True)
class VoiceLifecycle:
    end_requested: asyncio.Event = field(default_factory=asyncio.Event)
    drained: asyncio.Event = field(default_factory=asyncio.Event)


class VoiceLifecycles:
    """Registry of active voice connections, one per session."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active: dict[str, VoiceLifecycle] = {}

    async def claim(self, session_id: str) -> VoiceLifecycle | None:
        async with self._lock:
            if session_id in self._active:
                return None
            lifecycle = VoiceLifecycle()
            self._active[session_id] = lifecycle
            return lifecycle

    async def release(self, session_id: str, lifecycle: VoiceLifecycle) -> None:
        async with self._lock:
            if self._active.get(session_id) is lifecycle:
                self._active.pop(session_id, None)

    async def request_end(self, session_id: str) -> VoiceLifecycle | None:
        async with self._lock:
            lifecycle = self._active.get(session_id)
            if lifecycle is not None:
                lifecycle.end_requested.set()
            return lifecycle


class VoiceSocket:
    """Training: push-to-talk. The browser gates microphone frames with `user.turn.finish`."""

    interaction = "push_to_talk"

    def __init__(
        self,
        services: Container,
        lifecycles: VoiceLifecycles,
        websocket: WebSocket,
        session_id: str,
    ) -> None:
        self.services = services
        self.lifecycles = lifecycles
        self.websocket = websocket
        self.session_id = session_id
        self.send_lock = asyncio.Lock()
        self.processing_lock = asyncio.Lock()
        self.voice_failed = asyncio.Event()
        self.finalized = asyncio.Event()
        self.audio_buffer = bytearray()
        self.sample_rate = services.settings.audio_sample_rate
        # Set in run() once the session and its pinned case are resolved.
        self.lifecycle: VoiceLifecycle
        self.session: ConversationSession
        self.case: MedicalCase
        self.transcription_config: TranscriptionConfig

    async def run(self) -> None:
        websocket, services, session_id = self.websocket, self.services, self.session_id
        await websocket.accept()
        try:
            stack = services.resolve_voice_stack(services.repository.get_session(session_id))
        except AriError as exc:
            await self._refuse("The persisted voice stack is unavailable", str(exc), code=4403)
            return
        lifecycle = await self.lifecycles.claim(session_id)
        if lifecycle is None:
            await self._refuse("A voice connection is already active for this session", code=4409)
            return
        self.lifecycle = lifecycle
        try:
            if services.repository.get_session(session_id).status is SessionStatus.CREATED:
                services.orchestrator.activate(session_id)
            self.session = services.repository.get_session(session_id)
            self.case = services.orchestrator._case_for_session(self.session)
            self.transcription_config = TranscriptionConfig.for_case(self.case)
        except Exception as exc:
            with suppress(Exception):
                await self._refuse(str(exc), code=1011)
            lifecycle.drained.set()
            await self.lifecycles.release(session_id, lifecycle)
            return
        await self.send(
            "call.started",
            session_id=session_id,
            provider_mode=services.settings.provider_mode,
            voice_stack_id=stack.id,
            voice_stack_version=stack.version,
            models=dict(stack.models),
            interaction=self.interaction,
        )
        end_watcher = asyncio.create_task(self._end_on_http_request())
        try:
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        except RuntimeError as exc:
            if not self.voice_failed.is_set() and not is_websocket_disconnect_runtime(exc):
                raise
        finally:
            end_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await end_watcher
            await self._drain()
            await self.lifecycles.release(session_id, lifecycle)

    async def _refuse(self, message: str, detail: str | None = None, *, code: int) -> None:
        data: dict[str, object] = {"message": message}
        if detail is not None:
            data["detail"] = detail
        await self.websocket.send_json({"type": "voice.error", "data": data})
        await self.websocket.close(code=code)

    async def send(self, event_type: str, **data: object) -> bool:
        try:
            async with self.send_lock:
                await self.websocket.send_json({"type": event_type, "data": data})
        except (RuntimeError, WebSocketDisconnect):
            return False
        return True

    async def _receive_loop(self) -> None:
        max_buffer = self.sample_rate * 2 * MAX_TURN_SECONDS
        while True:
            message = await self.websocket.receive()
            if message.get("bytes") is not None:
                if not self.audio_buffer:
                    await self.send("user.speech_started")
                if len(self.audio_buffer) + len(message["bytes"]) <= max_buffer:
                    self.audio_buffer.extend(message["bytes"])
                continue
            raw_text = message.get("text")
            if raw_text is None:
                continue
            try:
                control = ClientControlMessage.model_validate_json(raw_text)
            except ValidationError as exc:
                await self.send("voice.error", message="Invalid control event", detail=str(exc))
                continue
            if control.type == "call.end":
                self.lifecycle.end_requested.set()
                await self._drain()
                await self.send("call.ended", session_id=self.session_id)
                return
            if control.type in {"audio.playback_started", "audio.playback_completed"}:
                await self._acknowledge_audio(control)
                continue
            if control.type == "turn.retry_tts" and control.turn_id:
                await self._retry_tts(control.turn_id)
                continue
            if control.type == "user.turn.finish":
                if not await self._guard(self._finish_turn()):
                    return
                continue
            if (
                control.type == "debug.transcript"
                and self.services.settings.provider_mode == "fake"
                and control.transcript
            ):
                await self.send("user.speech_started")
                if not await self._guard(self._process_final(control.transcript)):
                    return

    async def _guard(self, work: Coroutine[object, object, None]) -> bool:
        """Run one turn; an unexpected failure closes the connection cleanly."""
        try:
            await work
        except Exception as exc:
            self.voice_failed.set()
            await self.send("voice.error", message=str(exc))
            await self.websocket.close(code=1011)
            return False
        return True

    async def _finish_turn(self) -> None:
        pcm = bytes(self.audio_buffer)
        self.audio_buffer.clear()
        if len(pcm) < int(self.sample_rate * 2 * MIN_TURN_SECONDS):
            await self.send("user.turn.empty")
            return
        context = ExecutionContext(
            session_id=self.session.id,
            learner_id=self.session.learner_id,
            operation="speech_to_text",
            case_version=self.case.version,
            case_hash=self.case.content_hash,
            prompt_version=self.transcription_config.prompt_version,
            prompt_hash=self.transcription_config.prompt_hash,
        )
        try:
            transcription = await self.services.transcriber.transcribe(
                pcm,
                sample_rate=self.sample_rate,
                context=context,
                config=self.transcription_config,
            )
        except ProviderError as exc:
            self.services.repository.record_execution(cast(ExecutionRecord, exc.execution))
            await self.send(
                "user.turn.failed", message="La transcription a échoué", detail=str(exc)
            )
            return
        if not transcription.text:
            self.services.repository.record_execution(transcription.execution)
            await self.send("user.turn.empty")
            return
        await self._process_final(transcription.text, transcription.execution)

    async def _process_final(self, text: str, stt_execution: ExecutionRecord | None = None) -> None:
        repository, orchestrator = self.services.repository, self.services.orchestrator
        async with self.processing_lock:
            turn_id = new_id()
            if stt_execution is not None:
                repository.record_execution(replace(stt_execution, turn_id=turn_id))
            turn = orchestrator.reserve_transcript(self.session_id, text, turn_id=turn_id)
            await self.send("user.transcript_final", text=text)
            await self.send("turn.persisted", turn=public_turn(turn))
            try:
                await orchestrator.complete_turn(turn)
            except ProviderError as exc:
                failed = repository.get_session(self.session_id).turns[turn.sequence - 1]
                await self.send("turn.response_failed", turn=public_turn(failed))
                await self.send(
                    "voice.error",
                    message="La réponse du patient a échoué",
                    detail=str(exc),
                    retryable=True,
                    turn_id=turn.id,
                )
                return
            outcome = repository.get_session(self.session_id).turns[turn.sequence - 1]
            await self.send(
                "patient.response_selected",
                text=outcome.patient_text,
                turn_id=outcome.id,
                response_id=outcome.provider_response_id,
            )
            await self.send("patient.response_text", text=outcome.patient_text, turn_id=outcome.id)
            await self._stream_patient_audio(outcome)

    async def _stream_patient_audio(self, turn: ConversationTurn) -> ConversationTurn:
        repository, session_id = self.services.repository, self.session_id
        audio_stream_id = new_id()
        turn = repository.begin_audio_stream(session_id, turn.id, audio_stream_id)
        await self.send(
            "patient.audio_streaming",
            turn_id=turn.id,
            response_id=turn.provider_response_id,
            audio_stream_id=audio_stream_id,
            attempt=turn.audio_attempt,
        )
        context = ExecutionContext(
            session_id=self.session.id,
            learner_id=self.session.learner_id,
            operation="text_to_speech",
            case_version=self.case.version,
            case_hash=self.case.content_hash,
            turn_id=turn.id,
        )
        index = 0
        mime_type = PCM_MIME_TYPE
        tts_execution: ExecutionRecord | None = None
        all_sent = True
        pcm_remainder = b""
        try:
            async for audio_event in self.services.tts.stream(turn.patient_text, context):
                mime_type = audio_event.mime_type
                if audio_event.type == "chunk" and audio_event.data:
                    audio_bytes = pcm_remainder + audio_event.data
                    complete_length = len(audio_bytes) - (len(audio_bytes) % 2)
                    pcm_remainder = audio_bytes[complete_length:]
                    if complete_length == 0:
                        continue
                    chunk_sent = await self.send(
                        "patient.audio_chunk",
                        turn_id=turn.id,
                        response_id=turn.provider_response_id,
                        audio_stream_id=audio_stream_id,
                        audio=base64.b64encode(audio_bytes[:complete_length]).decode("ascii"),
                        mime_type=mime_type,
                        index=index,
                    )
                    all_sent = chunk_sent and all_sent
                    index += 1
                elif audio_event.execution is not None:
                    tts_execution = audio_event.execution
        except ProviderError as exc:
            repository.record_execution(cast(ExecutionRecord, exc.execution))
            failed = repository.mark_tts_failed(session_id, turn.id, audio_stream_id)
            await self.send("turn.tts_failed", turn=public_turn(failed), retryable=True)
            await self.send(
                "voice.error",
                message="La synthèse audio a échoué",
                detail=str(exc),
                retryable=True,
                turn_id=turn.id,
            )
            return failed
        incomplete = index == 0 or bool(pcm_remainder)
        if tts_execution is not None:
            if incomplete:
                tts_execution = replace(
                    tts_execution,
                    status=ExecutionStatus.FAILED,
                    error_code="invalid_pcm_audio",
                    error_message="TTS returned empty or incomplete PCM frames",
                    retryable=True,
                )
            repository.record_execution(tts_execution)
        if incomplete:
            failed = repository.mark_tts_failed(session_id, turn.id, audio_stream_id)
            await self.send("turn.tts_failed", turn=public_turn(failed), retryable=True)
            return failed
        if not all_sent:
            repository.mark_session_delivery_unconfirmed(session_id)
            return repository.get_session(session_id).turns[turn.sequence - 1]
        turn = repository.mark_audio_sent(session_id, turn.id, audio_stream_id, index)
        correlation = {
            "turn_id": turn.id,
            "response_id": turn.provider_response_id,
            "audio_stream_id": audio_stream_id,
            "mime_type": mime_type,
            "last_index": index - 1,
        }
        await self.send("patient.audio_sent", **correlation)
        await self.send("patient.audio_done", **correlation)
        await self.send("turn.completed", turn=public_turn(turn))
        return turn

    async def _retry_tts(self, turn_id: str) -> None:
        async with self.processing_lock:
            turns = self.services.repository.get_session(self.session_id).turns
            turn = next((item for item in turns if item.id == turn_id), None)
            if turn is None or turn.response_state not in {
                TurnResponseState.TTS_FAILED,
                TurnResponseState.DELIVERY_UNCONFIRMED,
            }:
                await self.send("turn.retry_rejected", turn_id=turn_id)
                return
            retried = await self._stream_patient_audio(turn)
            await self.send("turn.retry_completed", turn=public_turn(retried))

    async def _acknowledge_audio(self, control: ClientControlMessage) -> None:
        repository = self.services.repository
        started = control.type == "audio.playback_started"
        try:
            method = (
                repository.confirm_audio_started if started else repository.confirm_audio_delivered
            )
            turn = method(
                self.session_id,
                cast(str, control.turn_id),
                cast(str, control.audio_stream_id),
                provider_response_id=control.response_id,
                last_index=cast(int, control.last_index),
            )
            await self.send(
                "turn.audio_started" if started else "turn.delivered", turn=public_turn(turn)
            )
        except AriError as exc:
            await self.send("audio.ack_rejected", turn_id=control.turn_id, message=str(exc))

    async def _drain(self) -> None:
        if self.finalized.is_set():
            await self.lifecycle.drained.wait()
            return
        self.finalized.set()
        try:
            async with self.processing_lock:
                pass
        finally:
            self.services.repository.mark_session_delivery_unconfirmed(self.session_id)
            self.lifecycle.drained.set()

    async def _end_on_http_request(self) -> None:
        await self.lifecycle.end_requested.wait()
        await self._drain()
        await self.send("call.ended", session_id=self.session_id)
        with suppress(Exception):
            await self.websocket.close(code=1000)
