"""One WebSocket per active voice session: PCM in, transcript, patient text, PCM out.

Connection state lives in this process only, so the API must run as a single worker.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import cast

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ari.api.voice_session_dto import public_turn
from ari.application.contracts import ExecutionContext, TranscriptionConfig
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.schemas import ClientControlMessage
from ari.container import Container
from ari.domain.errors import (
    AriError,
    ConflictError,
    ProviderError,
    QuotaExceededError,
    UsageCeilingError,
)
from ari.domain.models import (
    ConversationSession,
    ConversationTurn,
    ExecutionRecord,
    ExecutionStatus,
    LearningMode,
    MedicalCase,
    SessionStatus,
    TurnResponseState,
    new_id,
    utc_now,
)

PCM_MIME_TYPE = "audio/pcm;rate=24000"
MAX_TURN_SECONDS = 300
MIN_TURN_SECONDS = 0.25

# Events that say nothing about the learner being there; every other event resets the idle clock.
PASSIVE_EVENTS = frozenset({"call.started", "call.ended", "session.time_limit", "voice.error"})
# WebSocket close codes of the limits, mirroring the HTTP statuses (409, 402, 429).
CLOSE_CONFLICT, CLOSE_QUOTA, CLOSE_CEILING = 4409, 4402, 4429
TIME_LIMIT_MESSAGES = {
    "duration": (
        "Le temps de l'épreuve est écoulé : la session se termine et son analyse démarre."
    ),
    "idle": (
        "Session fermée après une période sans activité. Votre échange est sauvegardé ; "
        "vous pouvez reprendre depuis l'historique."
    ),
}


def close_code_for(exc: Exception) -> int:
    if isinstance(exc, ConflictError):
        return CLOSE_CONFLICT
    if isinstance(exc, QuotaExceededError):
        return CLOSE_QUOTA
    if isinstance(exc, UsageCeilingError):
        return CLOSE_CEILING
    return 1011


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
        self._by_learner: dict[str, str] = {}

    async def claim(self, session_id: str, learner_id: str) -> VoiceLifecycle | None:
        """The connection of a session, or None if it already has one.

        A learner has one live voice connection at a time: a second session raises
        `ConflictError`. "Live" is the in-memory registry, not the session status, because a
        session stays `active` in the database after its browser tab is gone.
        """
        async with self._lock:
            if session_id in self._active:
                return None
            other = self._by_learner.get(learner_id)
            if other is not None and other != session_id:
                raise ConflictError(
                    "Une autre session vocale est déjà ouverte sur votre compte. "
                    "Terminez-la ou fermez son onglet avant d'en ouvrir une nouvelle."
                )
            lifecycle = VoiceLifecycle()
            self._active[session_id] = lifecycle
            self._by_learner[learner_id] = session_id
            return lifecycle

    async def release(self, session_id: str, lifecycle: VoiceLifecycle) -> None:
        async with self._lock:
            if self._active.get(session_id) is lifecycle:
                self._active.pop(session_id, None)
                for learner, held in list(self._by_learner.items()):
                    if held == session_id:
                        del self._by_learner[learner]

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
        # Set by an open-microphone socket when the learner speaks over the patient: the rest
        # of the answer's audio is not sent, so its facts are never heard nor credited.
        self.barge_in = asyncio.Event()
        self.sample_rate = services.settings.audio_sample_rate
        self.last_activity = time.monotonic()
        # Set in run() once the session and its pinned case are resolved.
        self.lifecycle: VoiceLifecycle
        self.session: ConversationSession
        self.case: MedicalCase
        self.transcription_config: TranscriptionConfig

    async def run(self) -> None:
        websocket, services, session_id = self.websocket, self.services, self.session_id
        await websocket.accept()
        try:
            opened = services.repository.get_session(session_id)
            stack = services.resolve_voice_stack(opened)
        except AriError as exc:
            await self._refuse("The persisted voice stack is unavailable", str(exc), code=4403)
            return
        try:
            lifecycle = await self.lifecycles.claim(session_id, opened.learner_id)
        except ConflictError as exc:
            await self._refuse(str(exc), code=close_code_for(exc))
            return
        if lifecycle is None:
            await self._refuse(
                "A voice connection is already active for this session", code=CLOSE_CONFLICT
            )
            return
        self.lifecycle = lifecycle
        try:
            if opened.status is SessionStatus.CREATED:
                # The quota counts calls that began: check again here, a learner may have
                # created several sessions before opening any.
                services.usage_guard.check_session_start(opened.learner_id, opened.learning_mode)
                services.orchestrator.activate(session_id)
            self.session = services.repository.get_session(session_id)
            self.case = services.orchestrator._case_for_session(self.session)
            self.transcription_config = TranscriptionConfig.for_case(self.case)
        except Exception as exc:
            with suppress(Exception):
                await self._refuse(str(exc), code=close_code_for(exc))
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
        limits_watcher = asyncio.create_task(self._enforce_limits())
        try:
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        except RuntimeError as exc:
            if not self.voice_failed.is_set() and not is_websocket_disconnect_runtime(exc):
                raise
        finally:
            for watcher in (limits_watcher, end_watcher):
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            await self._drain()
            await self.lifecycles.release(session_id, lifecycle)

    async def _refuse(self, message: str, detail: str | None = None, *, code: int) -> None:
        data: dict[str, object] = {"message": message}
        if detail is not None:
            data["detail"] = detail
        await self.websocket.send_json({"type": "voice.error", "data": data})
        await self.websocket.close(code=code)

    async def send(self, event_type: str, **data: object) -> bool:
        if event_type not in PASSIVE_EVENTS:
            self.last_activity = time.monotonic()
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
                # Push-to-talk frames only flow while the learner holds the button.
                self.last_activity = time.monotonic()
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
        await self._answer_utterance(pcm)

    async def _answer_utterance(self, pcm: bytes) -> None:
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
        interrupted = False
        pcm_remainder = b""
        try:
            async for audio_event in self._tts().stream(turn.patient_text, context):
                mime_type = audio_event.mime_type
                if audio_event.type == "chunk" and self.barge_in.is_set():
                    # Interrupted: keep reading to the end of the sentence so the call is
                    # recorded with its cost, but send nothing more to the browser.
                    interrupted = True
                elif audio_event.type == "chunk" and audio_event.data:
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
        if interrupted:
            if tts_execution is not None:
                self.services.repository.record_execution(tts_execution)
            # The turn stays undelivered: the session drain marks it unconfirmed, no credit.
            await self.send("patient.audio_interrupted", turn_id=turn.id, chunks_sent=index)
            return repository.get_session(session_id).turns[turn.sequence - 1]
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

    def _tts(self) -> StreamingTTSProvider:
        return self.services.tts

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

    def _time_limit_reason(self) -> str | None:
        """Why the connection must end now, if it must.

        An exam ends at the scenario's duration plus a technical margin, like the real exam;
        training has no cap. Both modes end after a period without any activity.
        """
        settings = self.services.settings
        if self.session.learning_mode is LearningMode.EXAM and self.session.call_started_at:
            allowed = (
                self.case.educational_target.duration_minutes + settings.session_grace_minutes
            ) * 60
            if (utc_now() - self.session.call_started_at).total_seconds() >= allowed:
                return "duration"
        if time.monotonic() - self.last_activity >= settings.voice_idle_timeout_seconds:
            return "idle"
        return None

    async def _enforce_limits(self) -> None:
        while (reason := self._time_limit_reason()) is None:
            await asyncio.sleep(1.0)
        await self.send("session.time_limit", reason=reason, message=TIME_LIMIT_MESSAGES[reason])
        # The same path as an end requested over HTTP: drain, call.ended, close. The browser
        # then asks for the analysis exactly as it does when the learner ends the call.
        self.lifecycle.end_requested.set()

    async def _end_on_http_request(self) -> None:
        await self.lifecycle.end_requested.wait()
        await self._drain()
        await self.send("call.ended", session_id=self.session_id)
        with suppress(Exception):
            await self.websocket.close(code=1000)
