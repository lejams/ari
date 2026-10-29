from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PatientResponseSchema(StrictModel):
    response_kind: Literal["sources", "unknown", "out_of_scope", "wrong_language"]
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
        if self.response_kind != "sources" and self.source_refs:
            raise ValueError("non-source responses cannot reference case sources")
        return self


# Exam turns that carry no clinical content: only these may use the patient's own words.
NON_CLINICAL_KINDS = ("small_talk", "clarification_request", "hesitation")


class ExamPatientResponseSchema(PatientResponseSchema):
    """The patient schema of the exam chain: the same sources, plus three non-clinical kinds.

    A non-clinical turn carries the patient's own short wording in `free_text`; the
    application still screens it (application/services/exam_session.py) before it is spoken.
    """

    response_kind: Literal[  # type: ignore[assignment]
        "sources",
        "unknown",
        "out_of_scope",
        "wrong_language",
        "small_talk",
        "clarification_request",
        "hesitation",
    ]
    free_text: Annotated[str, Field(max_length=160)] | None = None

    @model_validator(mode="after")
    def free_text_only_for_non_clinical_turns(self) -> Self:
        non_clinical = self.response_kind in NON_CLINICAL_KINDS
        if non_clinical and not (self.free_text or "").strip():
            raise ValueError("non-clinical responses require free_text")
        if not non_clinical and self.free_text is not None:
            raise ValueError("only non-clinical responses may carry free_text")
        return self


class EvidenceObservationSchema(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=350)]
    evidence_turn_sequences: list[int] = Field(min_length=1, max_length=5)


LANGUAGE_ERROR_CATEGORIES = (
    "gender",
    "case",
    "verb_form",
    "word_order",
    "word_choice",
    "register",
    "other",
)


class LanguageErrorSchema(EvidenceObservationSchema):
    """A meaningful spoken error with a closed category, so recurrences can be counted."""

    category: Literal[
        "gender", "case", "verb_form", "word_order", "word_choice", "register", "other"
    ]


class VocabularyCandidateSchema(StrictModel):
    lemma: Annotated[str, Field(min_length=1, max_length=80)]
    translation: Annotated[str, Field(min_length=1, max_length=120)]
    example: Annotated[str, Field(min_length=1, max_length=250)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence_turn_sequences: list[int] = Field(min_length=1, max_length=5)
    # missing: the learner needed it and did not have it; misused: said wrongly;
    # well_used: said correctly (never added to the lexicon, only promotes an entry).
    kind: Literal["missing", "misused", "well_used"] = "missing"


class CodeSwitchSchema(StrictModel):
    turn: int
    fragment: Annotated[str, Field(min_length=1, max_length=120)]
    # The key word or short phrase, in the simulation language, the learner needed.
    intended_term: Annotated[str, Field(min_length=1, max_length=80)] | None = None


class EmpathyJudgementSchema(StrictModel):
    moment_id: str
    verdict: Literal["acknowledged", "partial", "ignored"]
    feedback: Annotated[str, Field(min_length=1, max_length=350)]
    evidence_turn_sequences: list[int] = Field(min_length=1, max_length=2)


class EvaluationOutputSchema(StrictModel):
    summary: Annotated[str, Field(min_length=1, max_length=500)]
    strengths: list[EvidenceObservationSchema] = Field(default_factory=list, max_length=2)
    priorities: list[EvidenceObservationSchema] = Field(default_factory=list, max_length=3)
    language_errors: list[LanguageErrorSchema] = Field(default_factory=list, max_length=6)
    vocabulary_candidates: list[VocabularyCandidateSchema] = Field(
        default_factory=list, max_length=8
    )
    code_switches: list[CodeSwitchSchema] = Field(default_factory=list, max_length=8)
    empathy: list[EmpathyJudgementSchema] = Field(default_factory=list, max_length=8)


class SpeakingRatingSchema(StrictModel):
    """CEFR estimate of one short spoken production, from its transcript."""

    estimated_level: Literal["A1", "A2", "B1", "B2", "C1", "C2"]
    confidence: Annotated[float, Field(ge=0, le=1)]
    observations: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=4
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
        "user.turn.finish",
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
