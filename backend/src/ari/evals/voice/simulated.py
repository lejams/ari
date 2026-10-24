"""The simulated learner's voice, the speech-to-text that hears it, a paced patient voice.

In process, models are simulated but the audio path is real: the learner's question goes as
PCM through the socket, the push-to-talk buffer or the exam's speech segmenter, then to the
transcriber. The learner's "voice" encodes the text in the signal so that the segmenter hears
speech (a loud, steady level) and `SimulatedTranscriber` can recover the exact words: what the
patient answers is what the script asked, and any transcription difference is measurable.

Encoding: each UTF-8 byte is a block of constant samples (level 1000 + 100 x byte), the sign
alternating from one block to the next so that equal bytes stay distinct. Block boundaries
survive the segmenter's framing and pre-roll because decoding reads runs of equal samples.
"""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ari.application.contracts import AudioStreamEvent, ExecutionContext, TranscriptionConfig
from ari.application.ports.stt import Transcription
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id

RATE = 24_000
BLOCK_SAMPLES = 720  # 30 ms per byte: a short question still lasts long enough to be speech.
MIN_SPEECH_SECONDS = 0.6
FAKE_MODEL = "deterministic-fake-v1"


def _level(byte: int) -> int:
    return 1000 + 100 * byte


def encode_speech(text: str) -> bytes:
    """PCM16 mono 24 kHz carrying `text`, padded with spaces to a minimal duration."""
    data = text.encode("utf-8")
    minimum = int(MIN_SPEECH_SECONDS * RATE / BLOCK_SAMPLES)
    data += b" " * max(0, minimum - len(data))
    samples: list[int] = []
    for index, byte in enumerate(data):
        level = _level(byte) if index % 2 == 0 else -_level(byte)
        samples.extend([level] * BLOCK_SAMPLES)
    return struct.pack(f"<{len(samples)}h", *samples)


def silence(milliseconds: int) -> bytes:
    return b"\x00\x00" * (RATE * milliseconds // 1000)


def decode_speech(pcm: bytes) -> str:
    """The text of every encoded run in `pcm`; silence and partial runs are skipped."""
    count = len(pcm) // 2
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])
    data = bytearray()
    index = 0
    while index < count:
        value = samples[index]
        end = index
        while end < count and samples[end] == value:
            end += 1
        level = abs(value)
        if value and end - index >= BLOCK_SAMPLES // 2 and (level - 1000) % 100 == 0:
            byte = (level - 1000) // 100
            if 0 <= byte <= 255:
                data.append(byte)
        index = end
    return data.decode("utf-8", errors="replace").strip()


def _execution(
    context: ExecutionContext, operation: str, usage: dict[str, object]
) -> ExecutionRecord:
    return ExecutionRecord(
        id=new_id(),
        session_id=context.session_id,
        turn_id=context.turn_id,
        learner_id=context.learner_id,
        operation=operation,
        provider="fake",
        model=FAKE_MODEL,
        model_returned=FAKE_MODEL,
        status=ExecutionStatus.SUCCEEDED,
        prompt_version=context.prompt_version,
        prompt_hash=context.prompt_hash,
        case_version=context.case_version,
        case_hash=context.case_hash,
        latency_ms=0,
        usage=usage,
    )


class SimulatedTranscriber:
    """Hears the simulated learner's voice exactly; `mishear` simulates recognition errors."""

    def __init__(self, mishear: dict[str, str] | None = None) -> None:
        self._mishear = dict(mishear or {})

    async def transcribe(
        self,
        pcm16: bytes,
        *,
        sample_rate: int,
        context: ExecutionContext,
        config: TranscriptionConfig,
    ) -> Transcription:
        del config
        text = decode_speech(pcm16)
        for heard, written in self._mishear.items():
            text = text.replace(heard, written)
        seconds = round(len(pcm16) / (sample_rate * 2), 3)
        return Transcription(
            text, _execution(context, "speech_to_text", {"audio_seconds": seconds})
        )


@dataclass(slots=True)
class PacedTTS:
    """A patient voice that streams in real time, so that the learner can talk over it.

    Every 100 ms of audio is sent after a pause (`chunk_delay`), as a streaming voice would:
    without it, the whole answer would reach the browser before any interruption is heard.
    """

    chunk_delay: float = 0.03
    ms_per_character: int = 40
    max_seconds: float = 6.0
    calls: list[float] = field(default_factory=list)

    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        self.calls.append(time.monotonic())
        seconds = min(self.max_seconds, max(0.3, len(text) * self.ms_per_character / 1000))
        chunk = struct.pack("<h", 600) * (RATE // 10)
        for _ in range(max(1, int(seconds * 10))):
            await asyncio.sleep(self.chunk_delay)
            yield AudioStreamEvent("chunk", data=chunk, mime_type="audio/pcm;rate=24000")
        yield AudioStreamEvent(
            "completed",
            mime_type="audio/pcm;rate=24000",
            execution=_execution(context, "text_to_speech", {"characters": len(text)}),
        )
