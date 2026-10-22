"""Exam mode on the controlled chain (ARI_EXAM_ENGINE=chain): open microphone, one turn at a time.

Browser PCM flows continuously; `SpeechSegmenter` turns it into utterances. Each utterance
goes through the training pipeline (Whisper, patient fact selection, streamed speech) with the
exam patient and the persona's voice, in order, one at a time. When the learner speaks over
the patient, the rest of the answer is not sent and the browser stops its playback: those
facts were not heard, so they are not credited. Playback acknowledgements, idle and duration
limits and the drain are the training socket's.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass

from fastapi import WebSocket
from pydantic import ValidationError

from ari.api.voice_socket import VoiceLifecycles, VoiceSocket
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.schemas import ClientControlMessage
from ari.application.services.exam_session import ExamVoices, SpeechSegmenter
from ari.container import Container
from ari.domain.models import TurnResponseState

CLOSE_GRACE_SECONDS = 30


@dataclass(frozen=True, slots=True)
class RetryAudio:
    turn_id: str


class ChainExamSocket(VoiceSocket):
    interaction = "open_microphone"

    def __init__(
        self,
        services: Container,
        lifecycles: VoiceLifecycles,
        websocket: WebSocket,
        session_id: str,
    ) -> None:
        super().__init__(services, lifecycles, websocket, session_id)
        # Utterances (PCM), debug transcripts (str) and audio retries, in the learner's order.
        self._utterances: asyncio.Queue[bytes | str | RetryAudio | None] = asyncio.Queue()
        self._busy = False
        self._worker: asyncio.Task[None] | None = None

    def _tts(self) -> StreamingTTSProvider:
        voices: ExamVoices | None = self.services.exam_voices
        assert voices is not None  # resolve_voice_stack refuses chain sessions without voices
        return voices.for_case(self.case)

    async def _receive_loop(self) -> None:
        settings = self.services.settings
        segmenter = SpeechSegmenter(
            sample_rate=self.sample_rate,
            threshold=settings.exam_vad_threshold,
            silence_ms=settings.exam_vad_silence_ms,
            min_speech_ms=settings.exam_vad_min_speech_ms,
        )
        self._worker = asyncio.create_task(self._answer_in_order())
        while not self._worker.done():
            message = await self.websocket.receive()
            if message.get("bytes") is not None:
                for event in segmenter.feed(message["bytes"]):
                    if event.kind == "speech_started":
                        await self._speech_started()
                    else:
                        self._utterances.put_nowait(event.audio)
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
            elif control.type == "turn.retry_tts" and control.turn_id:
                # Through the worker: the microphone keeps being read while the audio streams.
                self._utterances.put_nowait(RetryAudio(control.turn_id))
            elif (
                control.type == "debug.transcript"
                and settings.provider_mode == "fake"
                and control.transcript
            ):
                await self._speech_started()
                self._utterances.put_nowait(control.transcript)

    async def _speech_started(self) -> None:
        # Speech is the learner's presence; microphone frames alone are not (they never stop).
        self.last_activity = time.monotonic()
        if self._busy:
            self.barge_in.set()
        await self.send("user.speech_started")

    async def _answer_in_order(self) -> None:
        while (item := await self._utterances.get()) is not None:
            self._busy = True
            # Speech heard from here on interrupts this item's answer; speech before it was the
            # utterance itself, or interrupted the previous answer.
            self.barge_in.clear()
            try:
                work: Coroutine[object, object, None]
                if isinstance(item, RetryAudio):
                    work = self._retry_failed_tts(item.turn_id)
                elif isinstance(item, str):
                    work = self._process_final(item)
                else:
                    work = self._answer_utterance(item)
                if not await self._guard(work):
                    return
            finally:
                self._busy = False

    async def _retry_failed_tts(self, turn_id: str) -> None:
        """A technical failure may be replayed; an interrupted answer is not, as in an exam."""
        turns = self.services.repository.get_session(self.session_id).turns
        turn = next((item for item in turns if item.id == turn_id), None)
        if turn is None or turn.response_state is not TurnResponseState.TTS_FAILED:
            await self.send("turn.retry_rejected", turn_id=turn_id)
            return
        await self._retry_tts(turn_id)

    async def _drain(self) -> None:
        """Utterances not yet started are dropped; the one in progress finishes and is saved."""
        if not self.finalized.is_set() and self._worker is not None:
            while not self._utterances.empty():
                self._utterances.get_nowait()
            self._utterances.put_nowait(None)
            try:
                async with asyncio.timeout(CLOSE_GRACE_SECONDS):
                    await self._worker
            except TimeoutError:
                self._worker.cancel()
                with suppress(asyncio.CancelledError):
                    await self._worker
        await super()._drain()
