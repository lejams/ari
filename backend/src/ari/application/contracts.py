from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, TypeVar

from pydantic import BaseModel

from ari.domain.models import ExecutionRecord, MedicalCase

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    session_id: str
    operation: str
    case_version: str
    case_hash: str
    prompt_version: str | None = None
    prompt_hash: str | None = None
    turn_id: str | None = None
    learner_id: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionConfig:
    expected_locales: tuple[str, ...]
    context_prompt: str
    prompt_version: str
    prompt_hash: str

    @classmethod
    def for_case(cls, case: MedicalCase) -> TranscriptionConfig:
        return cls(
            expected_locales=(case.language,),
            context_prompt=case.transcription_context,
            prompt_version=f"stt-context:{case.id}@{case.version}",
            prompt_hash=sha256(case.transcription_context.encode()).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class LLMRequest:
    messages: tuple[dict[str, str], ...]
    context: ExecutionContext


@dataclass(frozen=True, slots=True)
class ProviderResult[T]:
    value: T
    execution: ExecutionRecord


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    data: bytes
    mime_type: str


@dataclass(frozen=True, slots=True)
class AudioResult:
    value: SynthesizedAudio
    execution: ExecutionRecord


@dataclass(frozen=True, slots=True)
class AudioStreamEvent:
    type: Literal["chunk", "completed"]
    data: bytes | None = None
    mime_type: str = "audio/mpeg"
    execution: ExecutionRecord | None = None
