from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


class CEFRLevel(StrEnum):
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class LearningMode(StrEnum):
    TRAINING = "training"
    EXAM = "exam"


class SessionStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    ANALYSIS_PENDING = "analysis_pending"
    ANALYSIS_FAILED = "analysis_failed"
    COMPLETED = "completed"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class VocabularyState(StrEnum):
    """Personal lexicon lifecycle; every promotion needs learner evidence, never a guess."""

    IDENTIFIED = "identified"  # Seen once: evaluation candidate, unused case term, code switch.
    REVIEWED = "reviewed"  # Recalled at least once in a spaced-repetition review.
    USED = "used"  # Spoken spontaneously in a voice session after entering the lexicon.
    MASTERED = "mastered"  # Used in two distinct later sessions and reviewed three times.


class LexiconSource(StrEnum):
    EVALUATION_CANDIDATE = "evaluation_candidate"
    TERMINOLOGY_UNUSED = "terminology_unused"
    CODE_SWITCH = "code_switch"
    MANUAL = "manual"


class SrsRating(StrEnum):
    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"


class PatientResponseKind(StrEnum):
    SOURCES = "sources"
    UNKNOWN = "unknown"
    OUT_OF_SCOPE = "out_of_scope"
    WRONG_LANGUAGE = "wrong_language"  # Learner spoke another language than the simulation.


class AudioDeliveryStatus(StrEnum):
    PENDING = "pending"
    STARTED = "started"
    DELIVERED = "delivered"
    UNCONFIRMED = "unconfirmed"
    FAILED = "failed"


class TurnResponseState(StrEnum):
    TRANSCRIPT_RESERVED = "transcript_reserved"
    RESPONSE_SELECTED = "response_selected"
    AUDIO_STREAMING = "audio_streaming"
    AUDIO_SENT = "audio_sent"
    AUDIO_STARTED = "audio_started"
    AUDIO_DELIVERED = "audio_delivered"
    RESPONSE_FAILED = "response_failed"
    TTS_FAILED = "tts_failed"
    DELIVERY_UNCONFIRMED = "delivery_unconfirmed"


class DisclosureRule(StrEnum):
    SPONTANEOUS = "spontaneous"
    WHEN_ASKED = "when_asked"
    WHEN_EXPLICITLY_ASKED = "when_explicitly_asked"
    WHEN_ASKED_ABOUT_ACTIVITY_OR_FOOD = "when_asked_about_activity_or_food"
    AFTER_5_TO_8_MINUTES_OR_NEXT_STEPS = "after_5_to_8_minutes_or_when_next_steps_discussed"
    WHEN_HOSPITALIZATION_MENTIONED = "when_hospitalization_is_mentioned"


@dataclass(frozen=True, slots=True)
class LearningGoal:
    target_exam: str = "FSP"
    target_cefr: CEFRLevel = CEFRLevel.C1
    rubric_version: str = "fsp-anamnesis-v1"


@dataclass(frozen=True, slots=True)
class EducationalTarget:
    exam: str
    phase: str
    duration_minutes: int


@dataclass(frozen=True, slots=True)
class LearnerDetails:
    """What the learner declared plus what ARI estimated. Never a certified level."""

    declared_level: CEFRLevel | None = None
    level_source: str = "self"  # self | certificate
    certificate_issuer: str | None = None  # goethe | telc | osd | testdaf | dsh | other
    certificate_level: CEFRLevel | None = None
    certificate_date: date | None = None
    exam_date: date | None = None
    minutes_per_day: int = 30
    land: str | None = None
    situation: str | None = None  # doctor | student
    specialty: str | None = None
    estimated_level: CEFRLevel | None = None
    estimated_at: datetime | None = None
    placement_attempt_id: str | None = None
    maintenance_cadence_days: int = 30  # How often an acquired word comes back: 7 or 30 days.


@dataclass(frozen=True, slots=True)
class LearnerProfile:
    id: str
    goal: LearningGoal
    created_at: datetime = field(default_factory=utc_now)
    details: LearnerDetails = field(default_factory=LearnerDetails)


@dataclass(frozen=True, slots=True)
class MedicalFact:
    id: str
    category: str
    value: str
    patient_phrase: str
    disclosure: DisclosureRule
    polarity: str = "present"
    criticality: str = "normal"
    patient_phrase_variants: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssessmentItem:
    id: str
    label: str
    satisfied_by_fact_ids: tuple[str, ...]
    required: bool = False
    weight: float = 1.0
    criticality: str = "normal"
    dimension: str = "clinical_coverage"
    evidence_kind: str = "delivered_facts"
    satisfaction: str = "all"
    doctor_phrases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    id: str
    label: str
    max_score: int
    description: str


@dataclass(frozen=True, slots=True)
class TerminologyTerm:
    id: str
    german: str
    french: str | None = None


@dataclass(frozen=True, slots=True)
class EmpathyMomentSpec:
    id: str
    fact_id: str
    cue: str
    expected: str


@dataclass(frozen=True, slots=True)
class AnamnesisSectionSpec:
    id: str
    label: str
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MedicalCase:
    id: str
    version: str
    validation_status: str
    content_hash: str
    language: str
    transcription_context: str
    unknown_response: str
    out_of_scope_response: str
    title: str
    public_summary: str
    difficulty: str
    educational_target: EducationalTarget
    source_revealed_fact_ids: Mapping[str, tuple[str, ...]]
    opening_statement: str
    communication_style: str
    facts: tuple[MedicalFact, ...]
    rubric_version: str
    rubric: tuple[RubricCriterion, ...]
    assessment_items: tuple[AssessmentItem, ...] = ()
    training_snapshot: Mapping[str, str] = field(default_factory=dict)
    available_for_new_sessions: bool = True
    terminology: tuple[TerminologyTerm, ...] = ()
    empathy_moments: tuple[EmpathyMomentSpec, ...] = ()
    anamnesis_sections: tuple[AnamnesisSectionSpec, ...] = ()
    cefr: str | None = None  # Authored scenario level; the programme filters on it.

    @property
    def fact_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.facts)


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    id: str
    session_id: str
    sequence: int
    user_text: str
    patient_text: str
    revealed_fact_ids: tuple[str, ...]
    selected_fact_ids: tuple[str, ...] = ()
    provider_input_item_id: str | None = None
    provider_response_id: str | None = None
    provider_response_status: str = "completed"
    response_state: TurnResponseState = TurnResponseState.TRANSCRIPT_RESERVED
    delivery_status: AudioDeliveryStatus = AudioDeliveryStatus.PENDING
    audio_stream_id: str | None = None
    audio_attempt: int = 0
    expected_audio_chunks: int | None = None
    audio_sent_at: datetime | None = None
    audio_started_at: datetime | None = None
    audio_delivered_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    patient_response_kind: str = PatientResponseKind.SOURCES.value


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    text: str
    evidence_turn_sequences: tuple[int, ...]
    category: str | None = None  # Language errors only: gender, case, verb_form, ...


@dataclass(frozen=True, slots=True)
class CodeSwitch:
    """A fragment the learner said in another language than the simulation."""

    turn: int
    fragment: str
    intended_term: str | None = None  # The key term in the simulation language.


@dataclass(frozen=True, slots=True)
class Evaluation:
    schema_version: str
    prompt_version: str
    rubric_version: str
    overall_score: float
    max_score: float
    summary: str
    strengths: tuple[EvidenceObservation, ...]
    priorities: tuple[EvidenceObservation, ...]
    missed_fact_ids: tuple[str, ...]
    language_errors: tuple[EvidenceObservation, ...]
    criteria: tuple[dict[str, Any], ...]
    created_at: datetime = field(default_factory=utc_now)
    code_switches: tuple[CodeSwitch, ...] = ()
    # Deterministic section coverage (anamnesis-sections-v1), None when the scenario has none.
    structure: dict[str, Any] | None = None
    # One judgement per authored empathy moment: deterministic trigger, LLM verdict with evidence.
    empathy: tuple[dict[str, Any], ...] = ()
    # At most three concrete next steps, derived from deterministic signals only.
    next_actions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class SessionMetrics:
    schema_version: str
    clinical_coverage: float
    communication: float
    structure: float
    language: float
    vocabulary: float
    pronunciation_status: str = "not_assessed"


@dataclass(frozen=True, slots=True)
class VocabularyObservation:
    id: str
    session_id: str
    lemma: str
    translation: str
    example: str
    evidence_turn_sequences: tuple[int, ...]
    state: VocabularyState = VocabularyState.IDENTIFIED
    confidence: float = 0.0
    kind: str = "missing"  # missing | misused | well_used, as judged by the evaluator.


@dataclass(frozen=True, slots=True)
class SrsState:
    """Spaced-repetition schedule of one lexicon entry (see domain/srs.py)."""

    due_at: datetime
    interval_days: int = 0
    ease: float = 2.5
    repetitions: int = 0
    lapses: int = 0
    last_reviewed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LexiconEntry:
    """One word or phrase in a learner's personal lexicon, across all sessions."""

    id: str
    learner_id: str
    lemma_key: str
    lemma: str
    translation: str
    example: str
    source: LexiconSource
    state: VocabularyState
    srs: SrsState
    first_session_id: str | None = None
    last_session_id: str | None = None
    used_session_ids: tuple[str, ...] = ()
    archived: bool = False
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class LexiconReview:
    id: str
    entry_id: str
    event_id: str
    rating: SrsRating
    reviewed_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    id: str
    session_id: str
    operation: str
    provider: str
    model: str
    status: ExecutionStatus
    prompt_version: str | None
    prompt_hash: str | None
    case_version: str
    case_hash: str
    latency_ms: int
    usage: dict[str, Any]
    provider_request_id: str | None = None
    turn_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ConversationSession:
    id: str
    learner_id: str
    case_id: str
    case_version: str
    case_hash: str
    goal: LearningGoal
    voice_stack_id: str = "pipeline_economy"
    voice_stack_version: str = "1"
    voice_stack_config: Mapping[str, Any] = field(default_factory=dict)
    status: SessionStatus = SessionStatus.CREATED
    turns: tuple[ConversationTurn, ...] = ()
    evaluation: Evaluation | None = None
    metrics: SessionMetrics | None = None
    vocabulary: tuple[VocabularyObservation, ...] = ()
    executions: tuple[ExecutionRecord, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    call_started_at: datetime | None = None
    ended_at: datetime | None = None
    training_snapshot: Mapping[str, str] = field(default_factory=dict)
    learning_mode: LearningMode | None = None  # Historical sessions: unknown, not inferred.
    start_request_id: str | None = None
    start_request_hash: str | None = None


def new_id() -> str:
    return str(uuid4())
