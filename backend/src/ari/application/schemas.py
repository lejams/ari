from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PatientResponseSchema(StrictModel):
    response_kind: Literal["sources", "unknown", "out_of_scope"]
    source_refs: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_refs must be unique")
        return value

    @model_validator(mode="after")
    def facts_match_response_kind(self) -> Self:
        if self.response_kind == "sources" and not self.source_refs:
            raise ValueError("source responses require at least one source reference")
        if self.response_kind in {"unknown", "out_of_scope"} and self.source_refs:
            raise ValueError("non-source responses cannot reference case sources")
        return self


class EvidenceObservationSchema(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=350)]
    evidence_turn_sequences: list[int] = Field(min_length=1, max_length=5)


class CriterionResultSchema(StrictModel):
    criterion_id: str
    score: Annotated[int, Field(ge=0, le=5)]
    evidence_turn_sequences: list[int] = Field(min_length=1, max_length=5)
    feedback: Annotated[str, Field(min_length=1, max_length=350)]


class VocabularyCandidateSchema(StrictModel):
    lemma: Annotated[str, Field(min_length=1, max_length=80)]
    translation: Annotated[str, Field(min_length=1, max_length=120)]
    example: Annotated[str, Field(min_length=1, max_length=250)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence_turn_sequences: list[int] = Field(min_length=1, max_length=5)


class EvaluationOutputSchema(StrictModel):
    summary: Annotated[str, Field(min_length=1, max_length=500)]
    strengths: list[EvidenceObservationSchema] = Field(default_factory=list, max_length=2)
    priorities: list[EvidenceObservationSchema] = Field(default_factory=list, max_length=3)
    language_errors: list[EvidenceObservationSchema] = Field(default_factory=list, max_length=6)
    criteria: list[CriterionResultSchema]
    vocabulary_candidates: list[VocabularyCandidateSchema] = Field(
        default_factory=list, max_length=8
    )


class VoiceEventSchema(BaseModel):
    type: str
    data: dict[str, object] = Field(default_factory=dict)


class ClientControlMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "call.start",
        "call.end",
        "debug.transcript",
        "audio.playback_started",
        "audio.playback_completed",
        "turn.retry_tts",
    ]
    transcript: str | None = None
    turn_id: str | None = None
    response_id: str | None = None
    audio_stream_id: str | None = None
    last_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def correlated_delivery_event(self) -> Self:
        if self.type in {"audio.playback_started", "audio.playback_completed"} and (
            not self.turn_id or not self.audio_stream_id or self.last_index is None
        ):
            raise ValueError("Audio playback events require turn, stream, and chunk index")
        if self.type == "turn.retry_tts" and not self.turn_id:
            raise ValueError("TTS retry requires a turn id")
        return self
