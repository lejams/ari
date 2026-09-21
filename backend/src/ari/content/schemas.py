"""Structured outputs the content pipeline asks of the model. Strict, no free text.

`ExtractionOutput` is the AI-facing twin of `ProtocolRecord`: no identifiers, no pedagogy,
every field present but nullable. The extraction service assigns ids, applies what the
uploader declared and validates the result as a `ProtocolRecord`.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ari.content.domain.protocol import (
    Polarity,
    QuestionPart,
    RemovedKind,
    UncertaintyPath,
)
from ari.domain.clinical import AnamnesisSectionId
from ari.domain.geography import Land


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Short = Annotated[str, Field(min_length=1, max_length=300)]
Long = Annotated[str, Field(min_length=1, max_length=2000)]


class SegmentationSpan(StrictModel):
    page_from: int
    page_to: int
    # The first 60 to 120 characters of the protocol, copied verbatim: used to locate its
    # exact start in the page text, never paraphrased.
    start_marker: Annotated[str, Field(min_length=1, max_length=200)]
    title_hint: Short | None
    date_hint: Short | None
    land_hint: Short | None
    city_hint: Short | None
    confidence: Annotated[float, Field(ge=0, le=1)]
    notes: Short | None


class SegmentationOutput(StrictModel):
    spans: list[SegmentationSpan]
    unassigned_pages: list[int]
    warnings: list[Short]


class ExtractedLocation(StrictModel):
    land: Land | None
    city: Short | None
    exam_body: Short | None
    exam_date: Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] | None
    specialty: Short | None


class ExtractedPatient(StrictModel):
    age_years: int | None
    sex: Literal["female", "male", "other", "unknown"]
    occupation_de: Short | None
    presenting_complaint_de: Short | None
    summary_de: Long | None
    uncertainty: Short | None


class ExtractedAnamnesisItem(StrictModel):
    section: AnamnesisSectionId
    label_de: Short
    value_de: Long | None
    polarity: Polarity
    temporality: Short | None
    quote_de: Annotated[str, Field(min_length=1, max_length=200)] | None
    source_pages: list[int]
    uncertainty: Short | None


class ExtractedDiagnosis(StrictModel):
    suspected_de: Short | None
    differentials_de: list[Short]
    uncertainty: Short | None


class ExtractedQuestion(StrictModel):
    part: QuestionPart
    question_de: Long
    expected_answer_de: Long | None
    candidate_answer_de: Long | None
    source_pages: list[int]
    uncertainty: Short | None


class ExtractedArztArzt(StrictModel):
    presentation_summary_de: Long | None
    discussion_points_de: list[Short]
    uncertainty: Short | None


class ExtractedTerm(StrictModel):
    german: Short
    lay_german: Short | None
    french: Short | None
    asked: bool


class ExtractedOutcome(StrictModel):
    result: Literal["passed", "failed", "unknown"]
    examiner_feedback_de: list[Short]
    candidate_tips_de: list[Short]
    uncertainty: Short | None


class ExtractedOpenQuestion(StrictModel):
    text_fr: Long
    critical: bool


class ExtractedFieldUncertainty(StrictModel):
    path: UncertaintyPath
    reason_fr: Short


class ExtractedPseudonymisation(StrictModel):
    removed_kinds: list[RemovedKind]
    notes: list[Short]


class ExtractionOutput(StrictModel):
    location: ExtractedLocation
    patient: ExtractedPatient
    anamnesis: list[ExtractedAnamnesisItem]
    diagnosis: ExtractedDiagnosis
    examiner_questions: list[ExtractedQuestion]
    arzt_arzt: ExtractedArztArzt
    fachbegriffe: list[ExtractedTerm]
    arztbrief_notes_de: Long | None
    outcome: ExtractedOutcome
    unresolved_questions: list[ExtractedOpenQuestion]
    field_uncertainties: list[ExtractedFieldUncertainty]
    pseudonymisation: ExtractedPseudonymisation


# ----- bundle draft: the model fills a skeleton the generator built from a gold protocol -----
# Every id below comes from the skeleton; the generator refuses unknown or missing ids.


class DraftFactText(StrictModel):
    fact_id: str
    # One to three ways the simulated patient states this fact, in the persona's register.
    patient_phrases_de: Annotated[list[Short], Field(min_length=1, max_length=3)]
    translation_fr: Short | None


class DraftEmpathyMoment(StrictModel):
    fact_id: str
    cue_fr: Short
    expected_fr: Short


class DraftBehaviourItem(StrictModel):
    item_id: str
    # Exact phrases matched case-insensitively in the transcript.
    doctor_phrases: Annotated[list[Short], Field(min_length=1, max_length=6)]


class DraftPracticeAnswer(StrictModel):
    question_id: str
    expected_behavior: Short
    accepted_answers: Annotated[list[Short], Field(min_length=1, max_length=6)]
    coaching_fr: Short


class BundleDraftOutput(StrictModel):
    title_de: Short  # No person name, no institution.
    public_summary_fr: Long
    transcription_context_de: Long
    unknown_response_de: Short
    out_of_scope_response_de: Short
    persona_de: Short
    opening_de: Long
    opening_fact_ids: list[str]
    objectives_fr: Annotated[list[Short], Field(min_length=1, max_length=5)]
    facts: list[DraftFactText]
    empathy_moments: Annotated[list[DraftEmpathyMoment], Field(max_length=3)]
    behaviour_items: list[DraftBehaviourItem]
    practice_answers: list[DraftPracticeAnswer]
