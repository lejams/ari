"""Exam mode: open microphone relayed to one speech-to-speech model.

Browser PCM flows straight to the model; the model's audio flows straight back with the
same correlation ids and playback acknowledgements as the training pipeline. A turn is
persisted once both its learner transcript and its patient response are known, then an
LLM audit attributes the spoken answer to authored case facts so delivery can credit them.
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, replace

from fastapi import WebSocket
from pydantic import ValidationError

from ari.api.voice_session_dto import public_turn
from ari.api.voice_socket import PCM_MIME_TYPE, VoiceLifecycles, VoiceSocket
from ari.application.contracts import ExecutionContext
from ari.application.ports.realtime import RealtimeConnection, RealtimeEvent
from ari.application.schemas import ClientControlMessage
from ari.application.services.realtime_patient import realtime_instructions
from ari.container import Container
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, new_id

INAUDIBLE_TEXT = "[unverständlich]"
MISSING_PATIENT_TRANSCRIPT = "[Antwort ohne Transkript]"
CLOSE_GRACE_SECONDS = 30


@dataclass(slots=True)
class PendingResponse:
    """One patient answer being streamed before its turn exists in the database."""

    turn_id: str
    audio_stream_id: str
    response_id: str
    chunks: int = 0
    transcript: str = ""
    remainder: bytes = b""


class RealtimeVoiceSocket(VoiceSocket):
    interaction = "open_microphone"

    def __init__(
        self,
        services: Container,
        lifecycles: VoiceLifecycles,
        websocket: WebSocket,
        session_id: str,
    ) -> None:
        super().__init__(services, lifecycles, websocket, session_id)
        self._user_transcripts: deque[tuple[str | None, str]] = deque()
        self._responses: dict[str, PendingResponse] = {}
        self._completed: deque[tuple[PendingResponse, RealtimeEvent]] = deque()
        self._early_acks: dict[str, ClientControlMessage] = {}

    async def _receive_loop(self) -> None:
        services = self.services
        context = ExecutionContext(
            session_id=self.session.id,
            learner_id=self.session.learner_id,
            operation="speech_to_speech",
            case_version=self.case.version,
            case_hash=self.case.content_hash,
            prompt_version=services.realtime_prompt.version,
            prompt_hash=services.realtime_prompt.content_hash,
        )
        try:
            connection = await services.realtime.open(
                instructions=realtime_instructions(services.realtime_prompt, self.case),
                language=self.case.language.split("-")[0].lower(),
                context=context,
            )
        except ProviderError as exc:
            if isinstance(exc.execution, ExecutionRecord):
                services.repository.record_execution(exc.execution)
            raise
        forward = asyncio.create_task(self._guard(self._forward(connection)))
        try:
            while not forward.done():
                message = await self.websocket.receive()
                if message.get("bytes") is not None:
                    await connection.send_audio(message["bytes"])
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
                    await connection.close()
                    await self._settle(forward)
                    await self._drain()
                    await self.send("call.ended", session_id=self.session_id)
                    return
                if control.type in {"audio.playback_started", "audio.playback_completed"}:
                    await self._acknowledge_audio(control)
                elif control.type == "turn.retry_tts":
                    await self.send("turn.retry_rejected", turn_id=control.turn_id)
                elif (
                    control.type == "debug.transcript"
                    and services.settings.provider_mode == "fake"
                    and control.transcript
                ):
                    await connection.send_audio(control.transcript.encode("utf-8"))
        finally:
            await connection.close()
            await self._settle(forward)

    @staticmethod
    async def _settle(forward: asyncio.Task[bool]) -> None:
        """Let in-flight persistence finish once the model stream is closed."""
        try:
            async with asyncio.timeout(CLOSE_GRACE_SECONDS):
                await forward
        except TimeoutError:
            forward.cancel()
            with suppress(asyncio.CancelledError):
                await forward

    async def _forward(self, connection: RealtimeConnection) -> None:
        async for event in connection.events():
            if event.type == "speech_started":
                await self.send("user.speech_started")
            elif event.type == "user_transcript":
                self._user_transcripts.append((event.item_id, event.text.strip()))
                await self.send("user.transcript_final", text=event.text.strip() or INAUDIBLE_TEXT)
                await self._persist_ready()
            elif event.type == "patient_audio" and event.response_id:
                await self._relay_audio(event.response_id, event.audio)
            elif event.type == "patient_transcript" and event.response_id:
                self._pending(event.response_id).transcript = event.text.strip()
            elif event.type == "response_completed" and event.response_id:
                pending = self._responses.pop(event.response_id, None)
                self._completed.append((pending or self._pending_new(event.response_id), event))
                await self._persist_ready()
            elif event.type == "error":
                await self.send("voice.notice", message=event.message)

    @staticmethod
    def _pending_new(response_id: str) -> PendingResponse:
        return PendingResponse(new_id(), new_id(), response_id)

    def _pending(self, response_id: str) -> PendingResponse:
        pending = self._responses.get(response_id)
        if pending is None:
            pending = self._responses[response_id] = self._pending_new(response_id)
        return pending

    async def _relay_audio(self, response_id: str, audio: bytes) -> None:
        pending = self._pending(response_id)
        if pending.chunks == 0 and not pending.remainder:
            await self.send(
                "patient.audio_streaming",
                turn_id=pending.turn_id,
                response_id=response_id,
                audio_stream_id=pending.audio_stream_id,
                attempt=1,
            )
        audio_bytes = pending.remainder + audio
        complete_length = len(audio_bytes) - (len(audio_bytes) % 2)
        pending.remainder = audio_bytes[complete_length:]
        if complete_length == 0:
            return
        await self.send(
            "patient.audio_chunk",
            turn_id=pending.turn_id,
            response_id=response_id,
            audio_stream_id=pending.audio_stream_id,
            audio=base64.b64encode(audio_bytes[:complete_length]).decode("ascii"),
            mime_type=PCM_MIME_TYPE,
            index=pending.chunks,
        )
        pending.chunks += 1

    async def _persist_ready(self) -> None:
        """Learner transcripts and patient responses arrive independently; pair them in order."""
        while self._user_transcripts and self._completed:
            item_id, text = self._user_transcripts.popleft()
            pending, event = self._completed.popleft()
            async with self.processing_lock:
                await self._persist_turn(item_id, text or INAUDIBLE_TEXT, pending, event)

    async def _persist_turn(
        self, item_id: str | None, text: str, pending: PendingResponse, event: RealtimeEvent
    ) -> None:
        repository, session_id = self.services.repository, self.session_id
        turn = self.services.orchestrator.reserve_transcript(
            session_id, text, turn_id=pending.turn_id, provider_input_item_id=item_id
        )
        await self.send("turn.persisted", turn=public_turn(turn))
        if event.execution is not None:
            repository.record_execution(replace(event.execution, turn_id=turn.id))
        heard = pending.chunks > 0
        patient_text = pending.transcript or MISSING_PATIENT_TRANSCRIPT
        selected_fact_ids: tuple[str, ...] = ()
        if heard and pending.transcript:
            selected_fact_ids = await self._attribute(pending.transcript, turn.id)
        turn = repository.save_selected_response(
            turn.id,
            patient_text=patient_text if heard else "",
            selected_fact_ids=selected_fact_ids,
            provider_response_id=pending.response_id,
            provider_response_status="completed" if heard else "failed",
        )
        if not heard:
            await self.send("turn.completed", turn=public_turn(turn))
            return
        await self.send("patient.response_text", text=patient_text, turn_id=turn.id)
        repository.begin_audio_stream(session_id, turn.id, pending.audio_stream_id)
        turn = repository.mark_audio_sent(
            session_id, turn.id, pending.audio_stream_id, pending.chunks
        )
        correlation = {
            "turn_id": turn.id,
            "response_id": pending.response_id,
            "audio_stream_id": pending.audio_stream_id,
            "mime_type": PCM_MIME_TYPE,
            "last_index": pending.chunks - 1,
        }
        await self.send("patient.audio_sent", **correlation)
        await self.send("patient.audio_done", **correlation)
        await self.send("turn.completed", turn=public_turn(turn))
        early = self._early_acks.pop(turn.id, None)
        if early is not None:
            await super()._acknowledge_audio(early)

    async def _attribute(self, patient_text: str, turn_id: str) -> tuple[str, ...]:
        """Audit failures never lose the turn: the answer is kept, no fact is credited."""
        try:
            attribution = await self.services.attributor.attribute(
                session=self.session, case=self.case, patient_text=patient_text, turn_id=turn_id
            )
        except ProviderError as exc:
            if isinstance(exc.execution, ExecutionRecord):
                self.services.repository.record_execution(exc.execution)
            return ()
        self.services.repository.record_execution(attribution.execution)
        return attribution.selected_fact_ids

    async def _acknowledge_audio(self, control: ClientControlMessage) -> None:
        streaming = {pending.turn_id for pending in self._responses.values()}
        streaming.update(pending.turn_id for pending, _ in self._completed)
        if control.turn_id in streaming:
            # Playback can start before the turn is persisted; replay the ack afterwards.
            self._early_acks[control.turn_id] = control
            return
        await super()._acknowledge_audio(control)
