"""Plays one scripted session over the real voice WebSocket, as the browser would.

Training is push-to-talk: the question's frames, then `user.turn.finish`. Exam is an open
microphone: a little silence, the question, then enough silence for the speech segmenter to
end the utterance. For a step marked `interrupt`, the next question is spoken as soon as the
first audio chunk of this answer arrives. Playback is confirmed like the browser does, except
for a step marked `acknowledge: false`. Every event is matched to its step by turn id.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ari.evals.voice.script import Script, Step
from ari.evals.voice.simulated import encode_speech, silence

Mode = Literal["training", "exam"]
FRAME_BYTES = 9600  # 200 ms of PCM16 at 24 kHz, roughly what a browser sends.
TERMINAL = frozenset(
    {
        "patient.audio_done",
        "patient.audio_interrupted",
        "turn.response_failed",
        "turn.tts_failed",
        "user.turn.empty",
        "user.turn.failed",
    }
)


class SocketLike(Protocol):
    def send_bytes(self, data: bytes) -> None: ...

    def send_json(self, data: Any) -> None: ...

    def receive_json(self) -> Any: ...


@dataclass(slots=True)
class StepTrace:
    step: Step
    spoken_at: float | None = None  # End of the learner's speech, as the server receives it.
    transcript: str | None = None
    turn_id: str | None = None
    first_audio_at: float | None = None
    chunks: int = 0
    terminal: str | None = None
    acknowledged: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def latency_ms(self) -> int | None:
        if self.spoken_at is None or self.first_audio_at is None:
            return None
        return round((self.first_audio_at - self.spoken_at) * 1000)

    @property
    def done(self) -> bool:
        if self.terminal is None:
            return False
        return self.acknowledged or not self._acknowledges()

    def _acknowledges(self) -> bool:
        return self.terminal == "patient.audio_done" and self.step.acknowledge


class Driver:
    def __init__(self, socket: SocketLike, mode: Mode, *, vad_silence_ms: int) -> None:
        self._socket = socket
        self._mode = mode
        self._trailing_silence = vad_silence_ms + 300
        self._spoken: deque[StepTrace] = deque()  # Spoken, not yet persisted as a turn.
        self._by_turn: dict[str, StepTrace] = {}
        self._streams: dict[str, dict[str, Any]] = {}

    def play(self, script: Script) -> list[StepTrace]:
        traces = [StepTrace(step) for step in script.steps]
        self._wait("call.started")
        next_index = 0

        def speak_next() -> None:
            nonlocal next_index
            if next_index < len(traces):
                self._speak(traces[next_index])
                next_index += 1

        speak_next()
        while not all(trace.done for trace in traces):
            event = self._socket.receive_json()
            kind, data = event["type"], event.get("data", {})
            trace = self._trace_for(kind, data)
            if trace is None:
                continue
            if kind == "patient.audio_chunk":
                if trace.first_audio_at is None:
                    trace.first_audio_at = time.perf_counter()
                    if trace.step.interrupt and self._mode == "exam":
                        speak_next()  # Barge in with the next question.
                trace.chunks += 1
            elif kind == "patient.audio_done":
                trace.terminal = kind
                if trace.step.acknowledge:
                    self._acknowledge(data)
            elif kind in TERMINAL:
                trace.terminal = kind
            elif kind == "turn.delivered":
                trace.acknowledged = True
            elif kind == "voice.error":
                trace.errors.append(str(data.get("message")))
            if trace.done and next_index < len(traces) and all(t.done for t in traces[:next_index]):
                speak_next()
        self._socket.send_json({"type": "call.end"})
        self._wait("call.ended")
        return traces

    def _trace_for(self, kind: str, data: dict[str, Any]) -> StepTrace | None:
        if kind == "user.transcript_final":
            if self._spoken:
                self._spoken[0].transcript = str(data.get("text"))
            return None
        if kind == "turn.persisted":
            trace = self._spoken.popleft()
            trace.turn_id = str(data["turn"]["id"])
            self._by_turn[trace.turn_id] = trace
            return None
        if kind in {"user.turn.empty", "user.turn.failed"}:
            return self._spoken.popleft() if self._spoken else None
        if kind == "patient.audio_streaming":
            self._streams[str(data["turn_id"])] = data
        turn_id = data.get("turn_id") or (data.get("turn") or {}).get("id")
        if kind == "voice.error" and turn_id is None:
            return None
        return self._by_turn.get(str(turn_id)) if turn_id is not None else None

    def _speak(self, trace: StepTrace) -> None:
        speech = encode_speech(trace.step.utterance)
        if self._mode == "exam":
            audio = silence(200) + speech + silence(self._trailing_silence)
        else:
            audio = speech
        for offset in range(0, len(audio), FRAME_BYTES):
            self._socket.send_bytes(audio[offset : offset + FRAME_BYTES])
        if self._mode == "training":
            self._socket.send_json({"type": "user.turn.finish"})
        trace.spoken_at = time.perf_counter()
        self._spoken.append(trace)

    def _acknowledge(self, done: dict[str, Any]) -> None:
        streaming = self._streams[str(done["turn_id"])]
        correlation = {
            "turn_id": done["turn_id"],
            "response_id": streaming.get("response_id"),
            "audio_stream_id": done["audio_stream_id"],
        }
        self._socket.send_json({"type": "audio.playback_started", **correlation, "last_index": 0})
        self._socket.send_json(
            {
                "type": "audio.playback_completed",
                **correlation,
                "last_index": done["last_index"],
            }
        )

    def _wait(self, wanted: str) -> dict[str, Any]:
        while True:
            event = self._socket.receive_json()
            if event["type"] == wanted:
                return dict(event.get("data", {}))
            if event["type"] == "voice.error" and wanted == "call.started":
                raise RuntimeError(f"voice connection refused: {event.get('data')}")
