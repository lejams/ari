from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


class CEFRLevel(StrEnum):
    B2 = "B2"
    C1 = "C1"


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
    IDENTIFIED = "identified"
    USED = "used"
    RECALLED = "recalled"
    MASTERED = "mastered"


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
class LearnerProfile:
    id: str
    goal: LearningGoal
    created_at: datetime = field(default_factory=utc_now)


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


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    text: str
    evidence_turn_sequences: tuple[int, ...]


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
