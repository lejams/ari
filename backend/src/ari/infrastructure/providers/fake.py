from __future__ import annotations

import asyncio
import json
import math
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
    STTEvent,
    TranscriptionConfig,
)
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
        question = str(payload["doctor_latest_utterance"]).lower()
        blocked_terms = {
            "ignore the instructions",
            "system prompt",
            "chatgpt",
            "openai",
            "competitor",
            "internet",
            "weather",
            "football",
            "treatment of",
            "president",
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
        if any(term in question for term in blocked_terms):
            return {"response_kind": "out_of_scope", "source_refs": []}
        is_english = str(case["language"]).lower().startswith("en")
        if is_english:
            keyword_map = {
                "hello": ("opening_statement",),
                "name": ("demographics.name",),
                "old": ("demographics.age",),
                "age": ("demographics.age",),
                "work": ("demographics.occupation",),
                "job": ("demographics.occupation",),
                "when": ("symptom.onset",),
                "pain": ("symptom.location", "symptom.quality", "symptom.intensity"),
                "radiat": ("symptom.radiation",),
                "nause": ("symptom.nausea",),
                "fever": ("symptom.fever",),
                "history": ("history.hypertension",),
                "diabetes": ("history.diabetes_absent",),
                "medic": ("medication.ramipril",),
                "food allerg": ("allergy.food_none",),
                "allerg": ("allergy.penicillin",),
                "smok": ("social.smoking",),
                "alcohol": ("social.alcohol",),
                "famil": ("family.father",),
                "hospital": ("concern.hospitalization",),
                "operation": ("concern.surgery",),
            }
            patient_scope_terms = {
                "do you have",
                "did you have",
                "have you had",
                "are you",
                "your symptom",
                "your pain",
                "your health",
                "your family",
                "your urine",
                "your stool",
                "medication",
                "allerg",
                "medical history",
            }
        else:
            keyword_map = {
                "guten tag": ("opening_statement",),
                "hallo": ("opening_statement",),
                "heißen": ("demographics.name",),
                "name": ("demographics.name",),
                "wie alt": ("demographics.age",),
                "alter": ("demographics.age",),
                "beruf": ("demographics.occupation",),
                "arbeiten": ("demographics.occupation",),
                "seit wann": ("symptom.onset",),
                "schmerz": (
                    "symptom.location",
                    "symptom.onset",
                    "symptom.quality",
                    "symptom.intensity",
                ),
                "ausstrahl": ("symptom.radiation",),
                "übel": ("symptom.nausea",),
                "fieber": ("symptom.fever",),
                "vorkrank": ("history.hypertension",),
                "diabetes": ("history.diabetes_absent",),
                "medik": ("medication.ramipril",),
                "lebensmittelallerg": ("allergy.food_none",),
                "allerg": ("allergy.penicillin",),
                "rauch": ("social.smoking",),
                "alkohol": ("social.alcohol",),
                "famil": ("family.father",),
                "krankenhaus": ("concern.hospitalization",),
                "operiert": ("concern.surgery",),
                "operation": ("concern.surgery",),
                "wie geht es weiter": ("concern.surgery",),
            }
            patient_scope_terms = {
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
        wanted = next((refs for word, refs in keyword_map.items() if word in question), ())
        available_sources = cast(list[dict[str, str]], case["available_sources"])
        allowed_refs = {item["ref"] for item in available_sources}
        source_refs = [
            (
                source_ref
                if source_ref.startswith(("opening_", "demographics."))
                else f"fact:{source_ref}"
            )
            for source_ref in wanted
            if (
                source_ref
                if source_ref.startswith(("opening_", "demographics."))
                else f"fact:{source_ref}"
            )
            in allowed_refs
        ]
        state = cast(dict[str, object], payload.get("conversation_state", {}))
        previous = cast(list[dict[str, object]], payload["previous_transcript"])
        previously_revealed = {
            str(fact_id)
            for turn in previous
            for fact_id in cast(list[object], turn.get("revealed_fact_ids", []))
        }
        timed_concern = "fact:concern.surgery"
        if (
            int(str(state.get("elapsed_seconds", 0))) >= 300
            and timed_concern in allowed_refs
            and "concern.surgery" not in previously_revealed
            and timed_concern not in source_refs
        ):
            source_refs.append(timed_concern)
        if source_refs:
            response_kind = "sources"
        else:
            response_kind = (
                "unknown"
                if any(term in question for term in patient_scope_terms)
                else "out_of_scope"
            )
        return {
            "response_kind": response_kind,
            "source_refs": source_refs,
        }

    @staticmethod
    def _evaluation(payload: dict[str, object]) -> dict[str, object]:
        transcript = cast(list[dict[str, object]], payload["transcript"])
        rubric = cast(list[dict[str, object]], payload["rubric"])
        turns = [int(str(item["turn"])) for item in transcript]
        score = min(5, max(1, len(turns)))
        criteria = [
            {
                "criterion_id": str(item["id"]),
                "score": score,
                "evidence_turn_sequences": turns[-2:] or [1],
                "feedback": "Continuez à poser des questions ciblées et à reformuler clairement.",
            }
            for item in rubric
        ]
        is_english = str(payload.get("simulation_language", "")).lower().startswith("en")
        vocabulary = (
            {
                "lemma": "radiate",
                "translation": "irradier",
                "example": "Does the pain radiate to your shoulder?",
                "confidence": 0.8,
            }
            if is_english
            else {
                "lemma": "ausstrahlen",
                "translation": "irradier",
                "example": "Strahlen die Schmerzen in die Schulter aus?",
                "confidence": 0.8,
            }
        )
        return {
            "summary": (
                "Entretien technique compréhensible et structuré. Priorisez la couverture "
                "clinique et des formulations anglaises plus naturelles."
                if is_english
                else "Entretien compréhensible et structuré. Priorisez la couverture "
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
            "criteria": criteria,
            "vocabulary_candidates": [{**vocabulary, "evidence_turn_sequences": turns[-1:]}],
        }


class FakeSTTConnection:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[STTEvent | None] = asyncio.Queue()

    async def send_audio(self, pcm16: bytes) -> None:
        del pcm16

    async def emit_transcript(self, text: str, context: ExecutionContext) -> None:
        await self._queue.put(STTEvent(type="speech_started"))
        await self._queue.put(STTEvent(type="transcript_delta", text=text))
        await self._queue.put(
            STTEvent(
                type="transcript_final",
                text=text,
                execution=_execution(context, "speech_to_text", 1, {"audio_seconds": 0}),
            )
        )

    async def events(self) -> AsyncIterator[STTEvent]:
        while (event := await self._queue.get()) is not None:
            yield event

    async def close(self) -> None:
        await self._queue.put(None)


class FakeSTTProvider:
    async def connect(
        self, context: ExecutionContext, config: TranscriptionConfig
    ) -> FakeSTTConnection:
        del context, config
        return FakeSTTConnection()


class FakeTTSProvider:
    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        started = time.perf_counter()
        rate, duration = 24_000, min(0.22, 0.04 + len(text) / 2000)
        frames = bytearray()
        for index in range(int(rate * duration)):
            amplitude = int(1000 * math.sin(2 * math.pi * 440 * index / rate))
            frames.extend(struct.pack("<h", amplitude))
        execution = _execution(
            context,
            "text_to_speech",
            int((time.perf_counter() - started) * 1000),
            {"characters": len(text), "first_audio_ms": 0},
        )
        audio = bytes(frames)
        for offset in range(0, len(audio), 4096):
            yield AudioStreamEvent(
                "chunk",
                data=audio[offset : offset + 4096],
                mime_type="audio/pcm;rate=24000",
            )
        yield AudioStreamEvent("completed", mime_type="audio/pcm;rate=24000", execution=execution)
