from __future__ import annotations

import math
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


class CaseMode(StrEnum):
    FSP = "fsp"
    TECHNICAL_TEST = "technical_test"


class InteractionMode(StrEnum):
    GUIDED = "guided"
    IMMERSIVE = "immersive"


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


class VoiceMetricTransport(StrEnum):
    REALTIME = "realtime"
    PIPELINE = "pipeline"


class VoiceTurnMetricStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class ClockDomain(StrEnum):
    SERVER = "server"
    BROWSER = "browser"
    PROVIDER = "provider"


class CostStatus(StrEnum):
    EXACT = "exact"
    ESTIMATED = "estimated"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


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
    mode: CaseMode
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
    demographics: Mapping[str, str | int | float | bool]
    demographic_responses: Mapping[str, str]
    source_revealed_fact_ids: Mapping[str, tuple[str, ...]]
    opening_statement: str
    communication_style: str
    facts: tuple[MedicalFact, ...]
    rubric_version: str
    rubric: tuple[RubricCriterion, ...]
    schema_version: str = "clinical-case-v1"
    source_collection: str | None = None
    source_refs: tuple[str, ...] = ()
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
class VocabularyHint:
    id: str
    term: str
    translation: str


@dataclass(frozen=True, slots=True)
class VocabularyHintAsset:
    id: str
    version: str
    case_id: str
    case_version: str
    language: str
    translation_language: str
    hints: tuple[VocabularyHint, ...]


@dataclass(frozen=True, slots=True)
class VocabularyHintUsage:
    session_id: str
    hint_id: str
    asset_version: str
    usage_count: int
    first_used_at: datetime
    last_used_at: datetime


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
    estimated_cost_usd: float | None = None
    pricing_version: str = "unknown"
    cost_status: CostStatus = CostStatus.UNKNOWN
    cost_amount_usd: float | None = None
    cost_units: dict[str, float] = field(default_factory=dict)
    cost_assumptions: tuple[str, ...] = ()
    cost_unknown_reason: str | None = None
    provider_request_id: str | None = None
    turn_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cost_status", CostStatus(self.cost_status))
        for amount in (self.cost_amount_usd, self.estimated_cost_usd):
            if amount is not None and (not math.isfinite(amount) or amount < 0):
                raise ValueError("Execution cost must be finite and nonnegative")
        if self.cost_status is CostStatus.UNKNOWN and self.cost_amount_usd is not None:
            raise ValueError("Unknown cost cannot have an amount")
        # Older callers only populated estimated_cost_usd. Keep that value while
        # explicitly downgrading its precision instead of silently treating it as exact.
        if self.cost_amount_usd is None and self.estimated_cost_usd is not None:
            object.__setattr__(self, "cost_amount_usd", self.estimated_cost_usd)
            if self.cost_status is CostStatus.UNKNOWN:
                object.__setattr__(self, "cost_status", CostStatus.ESTIMATED)
                object.__setattr__(
                    self,
                    "cost_assumptions",
                    (*self.cost_assumptions, "legacy_estimated_cost_without_structured_units"),
                )
        if self.estimated_cost_usd is None and self.cost_amount_usd is not None:
            object.__setattr__(self, "estimated_cost_usd", self.cost_amount_usd)
        if self.cost_status is not CostStatus.UNKNOWN and self.cost_amount_usd is None:
            raise ValueError("Known, estimated, or partial execution cost requires an amount")


@dataclass(frozen=True, slots=True)
class VoiceTurnMetric:
    id: str
    session_id: str
    turn_id: str
    voice_stack_id: str
    transport: VoiceMetricTransport
    trace_id: str = ""
    voice_stack_version: str = "unknown"
    schema_version: str = "voice-turn-metric-v2"
    models: Mapping[str, str] = field(default_factory=dict)
    provider_ids: Mapping[str, str] = field(default_factory=dict)
    case_id: str | None = None
    case_version: str | None = None
    case_hash: str | None = None
    interaction_mode: InteractionMode | None = None
    prompt_versions: Mapping[str, str] = field(default_factory=dict)
    prompt_hashes: Mapping[str, str] = field(default_factory=dict)
    delivery_status: AudioDeliveryStatus = AudioDeliveryStatus.PENDING
    application_version: str | None = None
    clock_domains: Mapping[str, ClockDomain | str] = field(default_factory=dict)
    wall_timestamps_utc: Mapping[str, str] = field(default_factory=dict)
    speech_end_to_transcript_final_ms: int | None = None
    transcript_final_to_llm_first_token_ms: int | None = None
    llm_total_ms: int | None = None
    llm_complete_to_tts_first_byte_ms: int | None = None
    tts_total_ms: int | None = None
    speech_end_to_first_audio_sent_ms: int | None = None
    speech_end_to_audio_started_ms: int | None = None
    audio_sent_to_playback_started_ms: int | None = None
    speech_end_to_transcript_ms: int | None = None
    transcript_to_first_token_ms: int | None = None
    first_token_to_first_audio_ms: int | None = None
    speech_end_to_first_audio_ms: int | None = None
    turn_total_ms: int | None = None
    interruption_count: int = 0
    error_count: int = 0
    retry_count: int = 0
    status: VoiceTurnMetricStatus = VoiceTurnMetricStatus.COMPLETED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "transport", VoiceMetricTransport(self.transport))
        object.__setattr__(self, "status", VoiceTurnMetricStatus(self.status))
        object.__setattr__(self, "trace_id", self.trace_id or self.id)
        if self.interaction_mode is not None:
            object.__setattr__(self, "interaction_mode", InteractionMode(self.interaction_mode))
        object.__setattr__(self, "delivery_status", AudioDeliveryStatus(self.delivery_status))
        normalized_domains = {
            key: ClockDomain(value) for key, value in self.clock_domains.items()
        }
        object.__setattr__(self, "clock_domains", normalized_domains)
        aliases = {
            "speech_end_to_transcript_final_ms": self.speech_end_to_transcript_ms,
            "transcript_final_to_llm_first_token_ms": self.transcript_to_first_token_ms,
            "speech_end_to_first_audio_sent_ms": self.speech_end_to_first_audio_ms,
        }
        for field_name, legacy_value in aliases.items():
            if getattr(self, field_name) is None and legacy_value is not None:
                object.__setattr__(self, field_name, legacy_value)
                normalized_domains.setdefault(field_name, ClockDomain.SERVER)
        if (
            self.turn_total_ms is not None
            and "turn_total_ms" not in normalized_domains
            and any(value is not None for value in aliases.values())
        ):
            normalized_domains["turn_total_ms"] = ClockDomain.SERVER
        object.__setattr__(self, "clock_domains", normalized_domains)
        duration_fields = (
            "speech_end_to_transcript_final_ms",
            "transcript_final_to_llm_first_token_ms",
            "llm_total_ms",
            "llm_complete_to_tts_first_byte_ms",
            "tts_total_ms",
            "speech_end_to_first_audio_sent_ms",
            "speech_end_to_audio_started_ms",
            "audio_sent_to_playback_started_ms",
            "turn_total_ms",
        )
        for field_name in duration_fields:
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative")
            if (
                value is not None
                and field_name not in normalized_domains
                and self.schema_version != "voice-turn-metric-legacy-v1"
            ):
                raise ValueError(f"{field_name} requires an explicit clock domain")
        if min(self.interruption_count, self.error_count, self.retry_count) < 0:
            raise ValueError("Telemetry counters cannot be negative")


@dataclass(frozen=True, slots=True)
class ConversationSession:
    id: str
    learner_id: str
    case_id: str
    case_version: str
    case_hash: str
    goal: LearningGoal
    interaction_mode: InteractionMode = InteractionMode.GUIDED
    voice_stack_id: str = "pipeline_economy"
    voice_stack_version: str = "1"
    voice_stack_config: Mapping[str, Any] = field(default_factory=dict)
    status: SessionStatus = SessionStatus.CREATED
    turns: tuple[ConversationTurn, ...] = ()
    evaluation: Evaluation | None = None
    metrics: SessionMetrics | None = None
    vocabulary: tuple[VocabularyObservation, ...] = ()
    vocabulary_hint_usages: tuple[VocabularyHintUsage, ...] = ()
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
