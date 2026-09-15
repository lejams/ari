from __future__ import annotations

import asyncio
import json
import math
import re
import struct
import time
from collections.abc import AsyncIterator
from typing import TypeVar, cast

from pydantic import BaseModel

from ari.application.contracts import (
    AudioStreamEvent,
    ExecutionContext,
    LLMRequest,
    ProviderResult,
    TranscriptionConfig,
)
from ari.application.ports.realtime import RealtimeEvent
from ari.application.ports.stt import Transcription
from ari.application.schemas import (
    EvaluationOutputSchema,
    PatientResponseSchema,
)
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id

T = TypeVar("T", bound=BaseModel)


def _execution(
    context: ExecutionContext, operation: str, latency_ms: int, usage: dict[str, object]
) -> ExecutionRecord:
    return ExecutionRecord(
        id=new_id(),
        session_id=context.session_id,
        turn_id=context.turn_id,
        operation=operation,
        provider="fake",
        model="deterministic-fake-v1",
        status=ExecutionStatus.SUCCEEDED,
        prompt_version=context.prompt_version,
        prompt_hash=context.prompt_hash,
        case_version=context.case_version,
        case_hash=context.case_hash,
        latency_ms=latency_ms,
        usage=usage,
    )


BLOCKED_TERMS = frozenset(
    {
        "ignore the instructions",
        "system prompt",
        "chatgpt",
        "openai",
        "competitor",
        "internet",
        "tell me a joke",
        "ignoriere die anweisungen",
        "systemprompt",
        "konkurrenz",
        "wetter",
        "fußball",
        "therapie von",
        "präsident",
        "erzähl mir einen witz",
        "erzählen sie mir einen witz",
    }
)
PATIENT_SCOPE_TERMS = frozenset(
    {
        "haben sie",
        "hatten sie",
        "sind sie",
        "nehmen sie",
        "ihre beschwerden",
        "ihre schmerzen",
        "ihre gesundheit",
        "ihre familie",
        "ihr urin",
        "ihr stuhl",
        "medikament",
        "allerg",
        "vorgeschichte",
    }
)
# Question keywords mapped to substrings of fact ids or categories they should reveal.
KEYWORD_HINTS: dict[str, tuple[str, ...]] = {
    "name": ("identity", "name"),
    "heißen": ("identity", "name"),
    "wie alt": ("age", "identity"),
    "seit wann": ("onset",),
    "schmerz": ("symptom", "pain", "schmerz"),
    "medik": ("medication",),
    "allerg": ("allergy",),
    "fieber": ("fever",),
    "rauch": ("smoking",),
    "alkohol": ("alcohol",),
    "famil": ("family",),
    "beruf": ("occupation",),
    "grund": ("reason",),
    "warum": ("reason",),
    "beschwerden": ("reason", "symptom"),
}


class FakeLLMProvider:
    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        started = time.perf_counter()
        payload = json.loads(request.messages[-1]["content"])
        if response_model is PatientResponseSchema:
            result = self._patient(payload)
        elif response_model is EvaluationOutputSchema:
            result = self._evaluation(payload)
        else:
            raise TypeError(f"Unsupported fake schema: {response_model.__name__}")
        elapsed = int((time.perf_counter() - started) * 1000)
        value = response_model.model_validate(result)
        return ProviderResult(
            value=value,
            execution=_execution(
                request.context,
                request.context.operation,
                elapsed,
                {
                    "input_characters": len(request.messages[-1]["content"]),
                    "output_characters": len(value.model_dump_json()),
                },
            ),
        )

    @staticmethod
    def _patient(payload: dict[str, object]) -> dict[str, object]:
        case = cast(dict[str, object], payload["case"])
        # Patient simulation audits the doctor's question; fact attribution audits the
        # patient's own sentence. Both select case sources by keyword.
        utterance = payload.get("patient_utterance", payload.get("doctor_latest_utterance"))
        question = str(utterance).casefold()
        if any(term in question for term in BLOCKED_TERMS):
            return {"response_kind": "out_of_scope", "source_refs": []}
        allowed_refs = {
            item["ref"] for item in cast(list[dict[str, str]], case["available_sources"])
        }
        source_refs: list[str] = []
        if any(greeting in question for greeting in ("guten tag", "hallo")):
            source_refs.append("opening_statement")
        hinted = {
            hint
            for keyword, hints in KEYWORD_HINTS.items()
            if keyword in question
            for hint in hints
        }
        for fact in cast(list[dict[str, object]], case["facts"]):
            fact_id = str(fact["id"]).casefold()
            category = str(fact["category"]).casefold()
            category_tokens = {t for t in re.split(r"[._\-\s]+", category) if len(t) >= 3}
            mentioned = fact_id in question or any(token in question for token in category_tokens)
            if mentioned or any(hint in category or hint in fact_id for hint in hinted):
                ref = f"fact:{fact['id']}"
                if ref in allowed_refs:
                    source_refs.append(ref)
        if source_refs:
            kind = "sources"
        elif any(term in question for term in PATIENT_SCOPE_TERMS):
            kind = "unknown"
        else:
            kind = "out_of_scope"
        return {"response_kind": kind, "source_refs": source_refs}

    @staticmethod
    def _evaluation(payload: dict[str, object]) -> dict[str, object]:
        transcript = cast(list[dict[str, object]], payload["transcript"])
        rubric = cast(list[dict[str, object]], payload["rubric"])
        turns = [int(str(item["turn"])) for item in transcript]
        score = min(5, max(1, len(turns)))
        return {
            "summary": (
                "Entretien compréhensible et structuré. Priorisez la couverture "
                "clinique et des formulations allemandes plus naturelles."
            ),
            "strengths": [
                {"text": "Questions compréhensibles", "evidence_turn_sequences": turns[-1:]},
                {"text": "Ton professionnel", "evidence_turn_sequences": turns[-1:]},
            ],
            "priorities": [
                {
                    "text": "Explorer systématiquement les symptômes associés",
                    "evidence_turn_sequences": turns[-1:],
                },
                {
                    "text": "Reformuler les informations clés",
                    "evidence_turn_sequences": turns[-1:],
                },
            ],
            "language_errors": [],
            "criteria": [
                {
                    "criterion_id": str(item["id"]),
                    "score": score,
                    "evidence_turn_sequences": turns[-2:] or [1],
                    "feedback": "Continuez à poser des questions ciblées et à reformuler.",
                }
                for item in rubric
            ],
            "vocabulary_candidates": [
                {
                    "lemma": "ausstrahlen",
                    "translation": "irradier",
                    "example": "Strahlen die Schmerzen in die Schulter aus?",
                    "confidence": 0.8,
                    "evidence_turn_sequences": turns[-1:],
                }
            ],
        }


FAKE_TRANSCRIPT = "Seit wann haben Sie Schmerzen?"


class FakeTranscriber:
    """UTF-8 text sent as audio comes back verbatim; real audio yields a fixed question."""

    async def transcribe(
        self,
        pcm16: bytes,
        *,
        sample_rate: int,
        context: ExecutionContext,
        config: TranscriptionConfig,
    ) -> Transcription:
        del config
        try:
            text = pcm16.decode("utf-8").strip()
        except UnicodeDecodeError:
            text = FAKE_TRANSCRIPT
        if not text.isprintable() or not text:
            text = FAKE_TRANSCRIPT
        usage: dict[str, object] = {"audio_seconds": round(len(pcm16) / (sample_rate * 2), 3)}
        return Transcription(text, _execution(context, "speech_to_text", 1, usage))


def fake_tone(text: str) -> bytes:
    """A short 440 Hz PCM16 24 kHz tone whose length grows with the text."""
    rate, duration = 24_000, min(0.22, 0.04 + len(text) / 2000)
    frames = bytearray()
    for index in range(int(rate * duration)):
        amplitude = int(1000 * math.sin(2 * math.pi * 440 * index / rate))
        frames.extend(struct.pack("<h", amplitude))
    return bytes(frames)


class FakeTTSProvider:
    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        started = time.perf_counter()
        audio = fake_tone(text)
        execution = _execution(
            context,
            "text_to_speech",
            int((time.perf_counter() - started) * 1000),
            {"characters": len(text), "first_audio_ms": 0},
        )
        for offset in range(0, len(audio), 4096):
            yield AudioStreamEvent(
                "chunk",
                data=audio[offset : offset + 4096],
                mime_type="audio/pcm;rate=24000",
            )
        yield AudioStreamEvent("completed", mime_type="audio/pcm;rate=24000", execution=execution)


class FakeRealtimeConnection:
    """UTF-8 text sent as audio is one learner utterance; the patient echoes it back.

    Real PCM frames are treated as silence so the fake never answers noise.
    """

    def __init__(self, context: ExecutionContext) -> None:
        self._context = context
        self._queue: asyncio.Queue[RealtimeEvent | None] = asyncio.Queue()
        self._turns = 0

    async def send_audio(self, pcm16: bytes) -> None:
        try:
            text = pcm16.decode("utf-8").strip()
        except UnicodeDecodeError:
            return
        if not text or not text.isprintable():
            return
        self._turns += 1
        item_id, response_id = f"fake-item-{self._turns}", f"fake-response-{self._turns}"
        audio = fake_tone(text)
        for event in (
            RealtimeEvent("speech_started"),
            RealtimeEvent("user_transcript", item_id=item_id, text=text),
            RealtimeEvent("patient_audio", response_id=response_id, audio=audio),
            RealtimeEvent("patient_transcript", response_id=response_id, text=text),
            RealtimeEvent(
                "response_completed",
                response_id=response_id,
                execution=_execution(
                    self._context,
                    "speech_to_speech",
                    1,
                    {"response_status": "completed", "characters": len(text)},
                ),
            ),
        ):
            await self._queue.put(event)

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        while (event := await self._queue.get()) is not None:
            yield event

    async def close(self) -> None:
        await self._queue.put(None)


class FakeRealtimeEngine:
    def __init__(self) -> None:
        self.instructions: list[str] = []

    async def open(
        self, *, instructions: str, language: str, context: ExecutionContext
    ) -> FakeRealtimeConnection:
        del language
        self.instructions.append(instructions)
        return FakeRealtimeConnection(context)
