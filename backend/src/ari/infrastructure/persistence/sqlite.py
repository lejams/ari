from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import (
    AudioDeliveryStatus,
    CEFRLevel,
    ClockDomain,
    ConversationSession,
    ConversationTurn,
    CostStatus,
    Evaluation,
    EvidenceObservation,
    ExecutionRecord,
    ExecutionStatus,
    GroundingAudit,
    InteractionMode,
    LearnerProfile,
    LearningGoal,
    LearningMode,
    PatientOpening,
    PatientOpeningStatus,
    SessionMetrics,
    SessionStatus,
    TurnResponseState,
    VocabularyHintUsage,
    VocabularyObservation,
    VocabularyState,
    VoiceMetricTransport,
    VoiceProfile,
    VoiceStackTransition,
    VoiceTurnMetric,
    VoiceTurnMetricStatus,
    new_id,
    utc_now,
)
from ari.infrastructure.persistence.base import Base
from ari.infrastructure.persistence.clinical_rows import (
    ClinicalSessionPinRow,
    ScenarioRow,
    scenario_snapshot,
)
from ari.infrastructure.persistence.identity import ProfileCredentialRow as ProfileCredentialRow
from ari.infrastructure.persistence.practice_rows import PracticeRunRow as PracticeRunRow
from ari.infrastructure.persistence.voice_learning import VoiceLearningRow, VoiceStartRow


class LearnerRow(Base):
    __tablename__ = "learners"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    goal: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), index=True)
    case_id: Mapped[str] = mapped_column(String)
    case_version: Mapped[str] = mapped_column(String)
    case_hash: Mapped[str] = mapped_column(String)
    goal: Mapped[dict[str, Any]] = mapped_column(JSON)
    voice_profile: Mapped[str] = mapped_column(String, default=VoiceProfile.ECONOMY.value)
    interaction_mode: Mapped[str] = mapped_column(String, default=InteractionMode.GUIDED.value)
    voice_stack_id: Mapped[str] = mapped_column(String)
    voice_stack_version: Mapped[str] = mapped_column(String)
    voice_stack_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    call_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TurnRow(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence"),
        UniqueConstraint("session_id", "provider_input_item_id", name="uq_turn_session_input_item"),
        UniqueConstraint("session_id", "provider_response_id", name="uq_turn_session_response"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    user_text: Mapped[str] = mapped_column(Text)
    patient_text: Mapped[str] = mapped_column(Text)
    revealed_fact_ids: Mapped[list[str]] = mapped_column(JSON)
    selected_fact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider_input_item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_response_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    interrupted: Mapped[bool] = mapped_column(default=False)
    interruption_audio_end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_response_status: Mapped[str] = mapped_column(String, default="completed")
    response_state: Mapped[str] = mapped_column(
        String, default=TurnResponseState.TRANSCRIPT_RESERVED.value
    )
    delivery_status: Mapped[str] = mapped_column(String, default=AudioDeliveryStatus.PENDING.value)
    audio_stream_id: Mapped[str | None] = mapped_column(String, nullable=True)
    audio_attempt: Mapped[int] = mapped_column(Integer, default=0)
    expected_audio_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audio_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    audio_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    normalized_user_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    observed_response_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class PatientOpeningRow(Base):
    __tablename__ = "patient_openings"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    spoken_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String)
    provider_response_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroundingAuditRow(Base):
    __tablename__ = "grounding_audits"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    supported_fact_ids: Mapped[list[str]] = mapped_column(JSON)
    unsupported_claims: Mapped[list[str]] = mapped_column(JSON)
    severity: Mapped[str] = mapped_column(String)
    confidence: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvaluationRow(Base):
    __tablename__ = "evaluations"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class MetricsRow(Base):
    __tablename__ = "session_metrics"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class VocabularyRow(Base):
    __tablename__ = "vocabulary_observations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    lemma: Mapped[str] = mapped_column(String)
    translation: Mapped[str] = mapped_column(String)
    example: Mapped[str] = mapped_column(Text)
    evidence_turn_sequences: Mapped[list[int]] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String)
    confidence: Mapped[float]


class VocabularyHintUsageRow(Base):
    __tablename__ = "vocabulary_hint_usages"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    hint_id: Mapped[str] = mapped_column(String, primary_key=True)
    asset_version: Mapped[str] = mapped_column(String)
    usage_count: Mapped[int] = mapped_column(Integer)
    first_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionRow(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    operation: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    case_version: Mapped[str] = mapped_column(String)
    case_hash: Mapped[str] = mapped_column(String)
    latency_ms: Mapped[int] = mapped_column(Integer)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON)
    estimated_cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    pricing_version: Mapped[str] = mapped_column(String)
    cost_status: Mapped[str] = mapped_column(String, default=CostStatus.UNKNOWN.value)
    cost_amount_usd: Mapped[float | None] = mapped_column(nullable=True)
    cost_units: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    cost_assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    cost_unknown_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VoiceTurnMetricRow(Base):
    __tablename__ = "voice_turn_metrics"
    __table_args__ = (
        CheckConstraint(
            "transport IN ('realtime', 'pipeline')",
            name="ck_voice_turn_metrics_transport",
        ),
        CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_voice_turn_metrics_status",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String)
    trace_id: Mapped[str] = mapped_column(String, index=True)
    voice_stack_id: Mapped[str] = mapped_column(String)
    voice_stack_version: Mapped[str] = mapped_column(String)
    transport: Mapped[str] = mapped_column(String)
    models: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    provider_ids: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True)
    case_version: Mapped[str | None] = mapped_column(String, nullable=True)
    case_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    interaction_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_versions: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    prompt_hashes: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    delivery_status: Mapped[str] = mapped_column(String)
    application_version: Mapped[str | None] = mapped_column(String, nullable=True)
    clock_domains: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    wall_timestamps_utc: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    speech_end_to_transcript_final_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    transcript_final_to_llm_first_token_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    llm_total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_complete_to_tts_first_byte_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    tts_total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speech_end_to_first_audio_sent_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    speech_end_to_audio_started_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    audio_sent_to_playback_started_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    speech_end_to_transcript_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript_to_first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_to_first_audio_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speech_end_to_first_audio_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turn_total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interruption_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VoiceStackTransitionRow(Base):
    __tablename__ = "voice_stack_transitions"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "from_stack_id",
            "to_stack_id",
            "failure_execution_id",
            name="uq_voice_stack_transition_request",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    from_stack_id: Mapped[str] = mapped_column(String)
    from_stack_version: Mapped[str] = mapped_column(String)
    from_stack_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    to_stack_id: Mapped[str] = mapped_column(String)
    to_stack_version: Mapped[str] = mapped_column(String)
    to_stack_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String)
    failure_execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _goal(value: dict[str, Any]) -> LearningGoal:
    return LearningGoal(
        target_exam=str(value["target_exam"]),
        target_cefr=CEFRLevel(str(value["target_cefr"])),
        rubric_version=str(value["rubric_version"]),
    )


def _dt(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    del connection_record
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


class SqliteSessionRepository:
    """Synchronous repository; API calls are short and SQLite-local in the POC."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
        )
        event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)

    def create_learner(self, learner: LearnerProfile) -> LearnerProfile:
        with Session(self.engine) as db:
            db.add(
                LearnerRow(
                    id=learner.id, goal=_jsonable(learner.goal), created_at=learner.created_at
                )
            )
            db.commit()
        return learner

    def get_learner(self, learner_id: str) -> LearnerProfile:
        with Session(self.engine) as db:
            row = db.get(LearnerRow, learner_id)
            if row is None:
                raise NotFoundError(f"Learner {learner_id} was not found")
            return LearnerProfile(id=row.id, goal=_goal(row.goal), created_at=_dt(row.created_at))

    def update_learner(self, learner: LearnerProfile) -> LearnerProfile:
        with Session(self.engine) as db:
            row = db.get(LearnerRow, learner.id)
            if row is None:
                raise NotFoundError(f"Learner {learner.id} was not found")
            row.goal = _jsonable(learner.goal)
            db.commit()
        return learner

    def create_session(self, session: ConversationSession) -> ConversationSession:
        if (session.start_request_id is None) != (session.start_request_hash is None):
            raise InvalidStateError("Start request ID and hash must be supplied together")
        if session.start_request_id:
            existing = self.find_session_request(session.learner_id, session.start_request_id)
            if existing:
                if existing.start_request_hash != session.start_request_hash:
                    raise InvalidStateError("Clé de démarrage réutilisée avec une autre demande")
                return existing
        try:
            return self._insert_session(session)
        except IntegrityError:
            if session.start_request_id:
                existing = self.find_session_request(session.learner_id, session.start_request_id)
                if existing:
                    if existing.start_request_hash != session.start_request_hash:
                        raise InvalidStateError(
                            "Clé de démarrage concurrente incompatible",
                        ) from None
                    return existing
            raise

    def find_session_request(self, learner_id: str, request_id: str) -> ConversationSession | None:
        with Session(self.engine) as db:
            row = db.get(VoiceStartRow, (learner_id, request_id))
            session_id = row.session_id if row else None
        result = self.get_session(session_id) if session_id else None
        if result and result.learner_id != learner_id:
            raise InvalidStateError("Propriétaire de la demande incohérent")
        return result

    def _insert_session(self, session: ConversationSession) -> ConversationSession:
        with Session(self.engine) as db:
            snapshot = dict(session.training_snapshot)
            scenario = None
            if snapshot:
                db.execute(update(ScenarioRow).where(
                    ScenarioRow.id == snapshot.get("scenario_id"),
                    ScenarioRow.version == snapshot.get("scenario_version"),
                ).values(status=ScenarioRow.status))
                scenario = db.get(ScenarioRow, (
                    snapshot.get("scenario_id"), snapshot.get("scenario_version"),
                ))
                if (scenario is None or scenario.status != "published"
                    or scenario.phase != "arzt_patient" or snapshot != scenario_snapshot(scenario)
                    or (session.case_id, session.case_version, session.case_hash)
                    != (scenario.case_id, scenario.case_version, scenario.case_hash)):
                    raise InvalidStateError("Publication unavailable or session snapshot mismatch")
            db.add(
                SessionRow(
                    id=session.id,
                    learner_id=session.learner_id,
                    case_id=session.case_id,
                    case_version=session.case_version,
                    case_hash=session.case_hash,
                    goal=_jsonable(session.goal),
                    voice_profile=session.voice_profile.value,
                    interaction_mode=session.interaction_mode.value,
                    voice_stack_id=session.voice_stack_id,
                    voice_stack_version=session.voice_stack_version,
                    voice_stack_config=_jsonable(dict(session.voice_stack_config)),
                    status=session.status.value,
                    created_at=session.created_at,
                    call_started_at=session.call_started_at,
                    ended_at=session.ended_at,
                )
            )
            if session.start_request_id is not None:
                db.flush()
                db.add(VoiceStartRow(
                    learner_id=session.learner_id, request_id=session.start_request_id,
                    request_hash=session.start_request_hash, session_id=session.id,
                ))
            if session.learning_mode is not None:
                db.flush()
                db.add(VoiceLearningRow(session_id=session.id, mode=session.learning_mode.value))
            if scenario is not None:
                db.flush()
                db.add(ClinicalSessionPinRow(
                    session_id=session.id, scenario_id=scenario.id,
                    scenario_version=scenario.version, scenario_hash=scenario.content_hash,
                ))
            db.commit()
        return session

    def get_session(self, session_id: str) -> ConversationSession:
        with Session(self.engine) as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                raise NotFoundError(f"Session {session_id} was not found")
            turns = tuple(
                self._turn(r)
                for r in db.scalars(
                    select(TurnRow)
                    .where(TurnRow.session_id == session_id)
                    .order_by(TurnRow.sequence)
                )
            )
            executions = tuple(
                self._execution(r)
                for r in db.scalars(
                    select(ExecutionRow)
                    .where(ExecutionRow.session_id == session_id)
                    .order_by(ExecutionRow.created_at)
                )
            )
            vocab = tuple(
                self._vocabulary(r)
                for r in db.scalars(
                    select(VocabularyRow).where(VocabularyRow.session_id == session_id)
                )
            )
            hint_usages = tuple(
                self._vocabulary_hint_usage(r)
                for r in db.scalars(
                    select(VocabularyHintUsageRow)
                    .where(VocabularyHintUsageRow.session_id == session_id)
                    .order_by(VocabularyHintUsageRow.first_used_at)
                )
            )
            evaluation_row = db.get(EvaluationRow, session_id)
            metrics_row = db.get(MetricsRow, session_id)
            audits = tuple(
                self._grounding_audit(audit)
                for audit in db.scalars(
                    select(GroundingAuditRow)
                    .where(GroundingAuditRow.session_id == session_id)
                    .order_by(GroundingAuditRow.created_at)
                )
            )
            transitions = tuple(
                self._voice_stack_transition(item)
                for item in db.scalars(
                    select(VoiceStackTransitionRow)
                    .where(VoiceStackTransitionRow.session_id == session_id)
                    .order_by(VoiceStackTransitionRow.created_at)
                )
            )
            opening_row = db.get(PatientOpeningRow, session_id)
            learning = db.get(VoiceLearningRow, session_id)
            start = db.scalar(select(VoiceStartRow).where(VoiceStartRow.session_id == session_id))
            pin = db.get(ClinicalSessionPinRow, session_id)
            snapshot = {}
            if pin is not None:
                scenario = db.get(ScenarioRow, (pin.scenario_id, pin.scenario_version))
                if scenario is None or scenario.content_hash != pin.scenario_hash:
                    raise InvalidStateError("Pinned scenario is unavailable or corrupt")
                snapshot = scenario_snapshot(scenario)
            return ConversationSession(
                id=row.id,
                learner_id=row.learner_id,
                case_id=row.case_id,
                case_version=row.case_version,
                case_hash=row.case_hash,
                training_snapshot=snapshot,
                learning_mode=LearningMode(learning.mode) if learning else None,
                start_request_id=start.request_id if start else None,
                start_request_hash=start.request_hash if start else None,
                goal=_goal(row.goal),
                voice_profile=VoiceProfile(row.voice_profile),
                interaction_mode=InteractionMode(row.interaction_mode),
                voice_stack_id=row.voice_stack_id,
                voice_stack_version=row.voice_stack_version,
                voice_stack_config=dict(row.voice_stack_config),
                status=SessionStatus(row.status),
                turns=turns,
                evaluation=self._evaluation(evaluation_row.payload) if evaluation_row else None,
                metrics=SessionMetrics(**metrics_row.payload) if metrics_row else None,
                vocabulary=vocab,
                vocabulary_hint_usages=hint_usages,
                executions=executions,
                grounding_audits=audits,
                voice_stack_transitions=transitions,
                patient_opening=(
                    self._patient_opening(opening_row) if opening_row is not None else None
                ),
                created_at=_dt(row.created_at),
                call_started_at=(_dt(row.call_started_at) if row.call_started_at else None),
                ended_at=_dt(row.ended_at) if row.ended_at else None,
            )

    def list_sessions(self, learner_id: str) -> tuple[ConversationSession, ...]:
        with Session(self.engine) as db:
            ids = tuple(
                db.scalars(
                    select(SessionRow.id)
                    .where(SessionRow.learner_id == learner_id)
                    .order_by(SessionRow.created_at.desc())
                )
            )
        return tuple(self.get_session(session_id) for session_id in ids)

    def set_status(self, session_id: str, status: SessionStatus) -> None:
        with Session(self.engine) as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                raise NotFoundError(f"Session {session_id} was not found")
            row.status = status.value
            if status is SessionStatus.COMPLETED and row.ended_at is None:
                row.ended_at = utc_now()
            db.commit()

    def mark_call_started(self, session_id: str) -> None:
        with Session(self.engine) as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                raise NotFoundError(f"Session {session_id} was not found")
            if row.call_started_at is None:
                row.call_started_at = utc_now()
            db.commit()

    def mark_call_ended(self, session_id: str) -> None:
        with Session(self.engine) as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                raise NotFoundError(f"Session {session_id} was not found")
            if row.ended_at is None:
                row.ended_at = utc_now()
            db.commit()

    def append_turn(self, turn: ConversationTurn) -> None:
        with Session(self.engine) as db:
            db.add(
                TurnRow(
                    id=turn.id,
                    session_id=turn.session_id,
                    sequence=turn.sequence,
                    user_text=turn.user_text,
                    patient_text=turn.patient_text,
                    revealed_fact_ids=list(turn.revealed_fact_ids),
                    selected_fact_ids=list(turn.selected_fact_ids),
                    provider_input_item_id=turn.provider_input_item_id,
                    provider_response_id=turn.provider_response_id,
                    interrupted=turn.interrupted,
                    interruption_audio_end_ms=turn.interruption_audio_end_ms,
                    provider_response_status=turn.provider_response_status,
                    response_state=turn.response_state.value,
                    delivery_status=turn.delivery_status.value,
                    audio_stream_id=turn.audio_stream_id,
                    audio_attempt=turn.audio_attempt,
                    expected_audio_chunks=turn.expected_audio_chunks,
                    audio_sent_at=turn.audio_sent_at,
                    audio_started_at=turn.audio_started_at,
                    audio_delivered_at=turn.audio_delivered_at,
                    created_at=turn.created_at,
                    normalized_user_text=turn.normalized_user_text,
                    canonical_response=_jsonable(turn.canonical_response),
                    observed_response_text=turn.observed_response_text,
                )
            )
            db.commit()

    def append_turn_idempotent(self, turn: ConversationTurn) -> ConversationTurn:
        try:
            with Session(self.engine) as db:
                existing = db.get(TurnRow, turn.id)
                if existing is None and turn.provider_input_item_id is not None:
                    existing = db.scalar(
                        select(TurnRow).where(
                            TurnRow.session_id == turn.session_id,
                            TurnRow.provider_input_item_id == turn.provider_input_item_id,
                        )
                    )
                if existing is not None:
                    return self._turn(existing)
                db.add(
                    TurnRow(
                        id=turn.id,
                        session_id=turn.session_id,
                        sequence=turn.sequence,
                        user_text=turn.user_text,
                        patient_text=turn.patient_text,
                        revealed_fact_ids=list(turn.revealed_fact_ids),
                        selected_fact_ids=list(turn.selected_fact_ids),
                        provider_input_item_id=turn.provider_input_item_id,
                        provider_response_id=turn.provider_response_id,
                        interrupted=turn.interrupted,
                        interruption_audio_end_ms=turn.interruption_audio_end_ms,
                        provider_response_status=turn.provider_response_status,
                        response_state=turn.response_state.value,
                        delivery_status=turn.delivery_status.value,
                        audio_stream_id=turn.audio_stream_id,
                        audio_attempt=turn.audio_attempt,
                        expected_audio_chunks=turn.expected_audio_chunks,
                        audio_sent_at=turn.audio_sent_at,
                        audio_started_at=turn.audio_started_at,
                        audio_delivered_at=turn.audio_delivered_at,
                        created_at=turn.created_at,
                        normalized_user_text=turn.normalized_user_text,
                        canonical_response=_jsonable(turn.canonical_response),
                        observed_response_text=turn.observed_response_text,
                    )
                )
                db.commit()
        except IntegrityError:
            if turn.provider_input_item_id is not None:
                found_turn = self.get_turn_by_provider_input(
                    turn.session_id, turn.provider_input_item_id
                )
                if found_turn is not None:
                    return found_turn
            raise
        return turn

    def get_turn_by_provider_input(
        self, session_id: str, provider_input_item_id: str
    ) -> ConversationTurn | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(TurnRow).where(
                    TurnRow.session_id == session_id,
                    TurnRow.provider_input_item_id == provider_input_item_id,
                )
            )
            return self._turn(row) if row is not None else None

    def get_turn_by_provider_response(
        self, session_id: str, provider_response_id: str
    ) -> ConversationTurn | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(TurnRow).where(
                    TurnRow.session_id == session_id,
                    TurnRow.provider_response_id == provider_response_id,
                )
            )
            return self._turn(row) if row is not None else None

    def save_grounding_audit(self, audit: GroundingAudit) -> None:
        with Session(self.engine) as db:
            if db.get(GroundingAuditRow, audit.id) is not None:
                return
            db.add(
                GroundingAuditRow(
                    id=audit.id,
                    session_id=audit.session_id,
                    turn_id=audit.turn_id,
                    schema_version=audit.schema_version,
                    prompt_version=audit.prompt_version,
                    supported_fact_ids=list(audit.supported_fact_ids),
                    unsupported_claims=list(audit.unsupported_claims),
                    severity=audit.severity,
                    confidence=audit.confidence,
                    created_at=audit.created_at,
                )
            )
            db.commit()

    def save_selected_response(
        self,
        turn_id: str,
        *,
        patient_text: str,
        selected_fact_ids: tuple[str, ...],
        provider_response_id: str | None,
        provider_response_status: str,
        interrupted: bool = False,
        interruption_audio_end_ms: int | None = None,
        normalized_user_text: str | None = None,
        canonical_response: Mapping[str, Any] | None = None,
        observed_response_text: str | None = None,
    ) -> ConversationTurn:
        with Session(self.engine) as db:
            row = db.get(TurnRow, turn_id)
            if row is None:
                raise NotFoundError(f"Turn {turn_id} was not found")
            if row.response_state != TurnResponseState.TRANSCRIPT_RESERVED.value:
                if (
                    row.patient_text == patient_text
                    and tuple(row.selected_fact_ids) == selected_fact_ids
                    and (row.provider_response_id == provider_response_id
                         or (row.response_state == TurnResponseState.RESPONSE_SELECTED.value
                             and row.provider_response_id is None))
                ):
                    candidate_canonical = _jsonable(canonical_response)
                    if (
                        row.canonical_response is not None
                        and canonical_response is not None
                        and row.canonical_response != candidate_canonical
                    ):
                        raise InvalidStateError("Canonical response is immutable")
                    if row.provider_response_id is None:
                        row.provider_response_id = provider_response_id
                        row.provider_response_status = provider_response_status
                        row.normalized_user_text = normalized_user_text
                        row.canonical_response = row.canonical_response or candidate_canonical
                        row.observed_response_text = observed_response_text
                        row.interrupted = interrupted
                        row.interruption_audio_end_ms = interruption_audio_end_ms
                        if interrupted:
                            row.response_state = TurnResponseState.INTERRUPTED.value
                            row.delivery_status = AudioDeliveryStatus.UNCONFIRMED.value
                            row.revealed_fact_ids = []
                        elif provider_response_status not in {"completed", "succeeded"}:
                            row.response_state = TurnResponseState.RESPONSE_FAILED.value
                            row.delivery_status = AudioDeliveryStatus.FAILED.value
                            row.revealed_fact_ids = []
                        db.commit()
                        db.refresh(row)
                    return self._turn(row)
                raise InvalidStateError("Turn response has already been selected")
            row.patient_text = patient_text
            row.selected_fact_ids = list(dict.fromkeys(selected_fact_ids))
            row.revealed_fact_ids = []
            row.provider_response_id = provider_response_id
            row.provider_response_status = provider_response_status
            row.normalized_user_text = normalized_user_text
            row.canonical_response = _jsonable(canonical_response)
            row.observed_response_text = observed_response_text
            row.interrupted = interrupted
            row.interruption_audio_end_ms = interruption_audio_end_ms
            if interrupted:
                row.response_state = TurnResponseState.INTERRUPTED.value
                row.delivery_status = AudioDeliveryStatus.UNCONFIRMED.value
            elif provider_response_status not in {"completed", "succeeded"}:
                row.response_state = TurnResponseState.RESPONSE_FAILED.value
                row.delivery_status = AudioDeliveryStatus.FAILED.value
            else:
                row.response_state = TurnResponseState.RESPONSE_SELECTED.value
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def mark_turn_response_failed(self, turn_id: str) -> ConversationTurn:
        with Session(self.engine) as db:
            row = db.get(TurnRow, turn_id)
            if row is None:
                raise NotFoundError(f"Turn {turn_id} was not found")
            if row.response_state == TurnResponseState.RESPONSE_FAILED.value:
                return self._turn(row)
            if row.response_state != TurnResponseState.TRANSCRIPT_RESERVED.value:
                raise InvalidStateError("Cannot fail a selected response")
            row.response_state = TurnResponseState.RESPONSE_FAILED.value
            row.provider_response_status = "failed"
            row.delivery_status = AudioDeliveryStatus.FAILED.value
            row.selected_fact_ids = []
            row.revealed_fact_ids = []
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def record_observed_response(
        self, turn_id: str, observed_text: str, *, fidelity_matches: bool
    ) -> ConversationTurn:
        """Persist transport output and atomically revoke delivery on mismatch."""
        with Session(self.engine) as db:
            row = db.get(TurnRow, turn_id)
            if row is None:
                raise NotFoundError(f"Turn {turn_id} was not found")
            if row.observed_response_text is not None:
                if row.observed_response_text != observed_text:
                    raise InvalidStateError("Observed response changed")
                if (
                    not fidelity_matches
                    and row.response_state != TurnResponseState.RESPONSE_FAILED.value
                ):
                    row.response_state = TurnResponseState.RESPONSE_FAILED.value
                    row.provider_response_status = "canonical_response_mismatch"
                    row.delivery_status = AudioDeliveryStatus.FAILED.value
                    row.revealed_fact_ids = []
                    db.commit()
                    db.refresh(row)
                return self._turn(row)
            row.observed_response_text = observed_text
            if not fidelity_matches:
                row.response_state = TurnResponseState.RESPONSE_FAILED.value
                row.provider_response_status = "canonical_response_mismatch"
                row.delivery_status = AudioDeliveryStatus.FAILED.value
                row.revealed_fact_ids = []
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def finalize_transport_response(
        self,
        turn_id: str,
        *,
        provider_response_id: str,
        provider_response_status: str,
        canonical_response: Mapping[str, Any] | None,
        observed_response_text: str | None,
        fidelity_matches: bool,
        interrupted: bool = False,
        interruption_audio_end_ms: int | None = None,
    ) -> ConversationTurn:
        """Atomically attach a transport response and settle its delivery state.

        Realtime output is never authoritative: only the persisted canonical
        response can populate ``patient_text``.  Repeated provider events are
        idempotent, while a changed canonical response is rejected.
        """
        with Session(self.engine) as db:
            row = db.get(TurnRow, turn_id)
            if row is None:
                raise NotFoundError(f"Turn {turn_id} was not found")
            if row.provider_response_id not in {None, provider_response_id}:
                raise InvalidStateError("Turn response has already been correlated")
            candidate = _jsonable(canonical_response)
            if row.provider_response_id == provider_response_id:
                if row.canonical_response != candidate:
                    raise InvalidStateError("Canonical response is immutable")
                if row.observed_response_text != observed_response_text:
                    raise InvalidStateError("Observed response changed")
                if row.interrupted != interrupted:
                    raise InvalidStateError("Transport interruption changed")
                if row.interruption_audio_end_ms != interruption_audio_end_ms:
                    raise InvalidStateError("Interruption metadata changed")
                status_matches = row.provider_response_status == provider_response_status
                # Mismatch/missing-canonical statuses are terminal classifications
                # derived from the original provider status.  Accept replay of the
                # same event while retaining that classification and state.
                status_matches = status_matches or (
                    row.provider_response_status
                    in {"canonical_response_mismatch", "canonical_response_missing"}
                    and provider_response_status in {"completed", "succeeded"}
                )
                if not status_matches:
                    raise InvalidStateError("Provider response status changed")
                if (
                    row.response_state == TurnResponseState.RESPONSE_FAILED.value
                    and row.provider_response_status
                    in {"canonical_response_mismatch", "canonical_response_missing"}
                    and fidelity_matches
                ):
                    raise InvalidStateError("Transport fidelity result changed")
                return self._turn(row)
            canonical_text = (
                str(candidate.get("text", "")).strip()
                if isinstance(candidate, dict)
                else ""
            )
            if row.canonical_response is not None and candidate is not None:
                if row.canonical_response != candidate:
                    raise InvalidStateError("Canonical response is immutable")
            elif row.canonical_response is not None:
                candidate = row.canonical_response
                canonical_text = str(candidate.get("text", "")).strip()

            row.provider_response_id = provider_response_id
            row.provider_response_status = provider_response_status
            row.interrupted = interrupted
            row.interruption_audio_end_ms = interruption_audio_end_ms
            row.observed_response_text = observed_response_text
            if candidate is not None and canonical_text:
                row.canonical_response = candidate
                row.patient_text = canonical_text

            canonical_valid = candidate is not None and bool(canonical_text)
            success = (
                canonical_valid
                and fidelity_matches
                and not interrupted
                and provider_response_status in {"completed", "succeeded"}
            )
            if interrupted:
                row.response_state = TurnResponseState.INTERRUPTED.value
                row.delivery_status = AudioDeliveryStatus.UNCONFIRMED.value
                row.revealed_fact_ids = []
            elif success:
                row.response_state = TurnResponseState.RESPONSE_SELECTED.value
                row.delivery_status = AudioDeliveryStatus.PENDING.value
                row.revealed_fact_ids = []
            else:
                row.response_state = TurnResponseState.RESPONSE_FAILED.value
                row.delivery_status = AudioDeliveryStatus.FAILED.value
                row.revealed_fact_ids = []
                if not canonical_valid and provider_response_status in {"completed", "succeeded"}:
                    row.provider_response_status = "canonical_response_missing"
                elif observed_response_text is not None and not fidelity_matches:
                    row.provider_response_status = "canonical_response_mismatch"
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def begin_audio_stream(
        self, session_id: str, turn_id: str, audio_stream_id: str
    ) -> ConversationTurn:
        with Session(self.engine) as db:
            row = db.get(TurnRow, turn_id)
            if row is None:
                raise NotFoundError(f"Turn {turn_id} was not found")
            if row.session_id != session_id or not audio_stream_id:
                raise InvalidStateError("Invalid audio stream correlation")
            if (
                row.response_state == TurnResponseState.AUDIO_STREAMING.value
                and row.audio_stream_id == audio_stream_id
            ):
                return self._turn(row)
            if row.response_state not in {
                TurnResponseState.RESPONSE_SELECTED.value,
                TurnResponseState.TTS_FAILED.value,
                TurnResponseState.DELIVERY_UNCONFIRMED.value,
            }:
                raise InvalidStateError(f"Cannot start audio from {row.response_state}")
            row.response_state = TurnResponseState.AUDIO_STREAMING.value
            row.delivery_status = AudioDeliveryStatus.PENDING.value
            row.audio_stream_id = audio_stream_id
            row.audio_attempt += 1
            row.expected_audio_chunks = None
            row.audio_sent_at = None
            row.audio_started_at = None
            row.audio_delivered_at = None
            row.revealed_fact_ids = []
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def mark_audio_sent(
        self, session_id: str, turn_id: str, audio_stream_id: str, chunk_count: int
    ) -> ConversationTurn:
        if chunk_count <= 0:
            raise InvalidStateError("At least one audio chunk is required")
        with Session(self.engine) as db:
            row = self._audio_row(db, session_id, turn_id, audio_stream_id)
            if row.response_state in {
                TurnResponseState.AUDIO_SENT.value,
                TurnResponseState.AUDIO_STARTED.value,
                TurnResponseState.AUDIO_DELIVERED.value,
            }:
                if row.expected_audio_chunks != chunk_count:
                    raise InvalidStateError("Audio chunk count changed")
                return self._turn(row)
            if row.response_state != TurnResponseState.AUDIO_STREAMING.value:
                raise InvalidStateError(f"Cannot mark audio sent from {row.response_state}")
            row.response_state = TurnResponseState.AUDIO_SENT.value
            row.expected_audio_chunks = chunk_count
            row.audio_sent_at = utc_now()
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def mark_tts_failed(
        self, session_id: str, turn_id: str, audio_stream_id: str
    ) -> ConversationTurn:
        with Session(self.engine) as db:
            row = self._audio_row(db, session_id, turn_id, audio_stream_id)
            if row.response_state == TurnResponseState.TTS_FAILED.value:
                return self._turn(row)
            if row.response_state != TurnResponseState.AUDIO_STREAMING.value:
                raise InvalidStateError(f"Cannot fail TTS from {row.response_state}")
            row.response_state = TurnResponseState.TTS_FAILED.value
            row.delivery_status = AudioDeliveryStatus.FAILED.value
            row.revealed_fact_ids = []
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def confirm_audio_started(
        self,
        session_id: str,
        turn_id: str,
        audio_stream_id: str,
        *,
        provider_response_id: str | None,
        last_index: int,
    ) -> ConversationTurn:
        with Session(self.engine) as db:
            row = self._validated_ack(
                db, session_id, turn_id, audio_stream_id, provider_response_id, last_index, False
            )
            if row.response_state in {
                TurnResponseState.AUDIO_STARTED.value,
                TurnResponseState.AUDIO_DELIVERED.value,
            }:
                return self._turn(row)
            if row.response_state != TurnResponseState.AUDIO_SENT.value:
                raise InvalidStateError(f"Cannot confirm playback from {row.response_state}")
            row.response_state = TurnResponseState.AUDIO_STARTED.value
            row.delivery_status = AudioDeliveryStatus.STARTED.value
            row.audio_started_at = utc_now()
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def confirm_audio_delivered(
        self,
        session_id: str,
        turn_id: str,
        audio_stream_id: str,
        *,
        provider_response_id: str | None,
        last_index: int,
    ) -> ConversationTurn:
        with Session(self.engine) as db:
            session = db.get(SessionRow, session_id)
            if session is None:
                raise NotFoundError(f"Session {session_id} was not found")
            if session.voice_stack_config.get("transport") != "pipeline":
                raise InvalidStateError("Full delivery is not observable for Realtime")
            row = self._validated_ack(
                db, session_id, turn_id, audio_stream_id, provider_response_id, last_index, True
            )
            if row.response_state == TurnResponseState.AUDIO_DELIVERED.value:
                return self._turn(row)
            if row.response_state != TurnResponseState.AUDIO_STARTED.value:
                raise InvalidStateError(f"Cannot confirm delivery from {row.response_state}")
            now = utc_now()
            row.response_state = TurnResponseState.AUDIO_DELIVERED.value
            row.delivery_status = AudioDeliveryStatus.DELIVERED.value
            row.audio_started_at = row.audio_started_at or now
            row.audio_delivered_at = now
            row.revealed_fact_ids = list(dict.fromkeys(row.selected_fact_ids))
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def mark_session_delivery_unconfirmed(self, session_id: str) -> None:
        with Session(self.engine) as db:
            rows = tuple(db.scalars(select(TurnRow).where(TurnRow.session_id == session_id)))
            for row in rows:
                if row.response_state in {
                    TurnResponseState.RESPONSE_SELECTED.value,
                    TurnResponseState.AUDIO_STREAMING.value,
                    TurnResponseState.AUDIO_SENT.value,
                    TurnResponseState.AUDIO_STARTED.value,
                }:
                    row.response_state = TurnResponseState.DELIVERY_UNCONFIRMED.value
                    row.delivery_status = AudioDeliveryStatus.UNCONFIRMED.value
                    row.revealed_fact_ids = []
            db.commit()

    def update_turn_selected_fact_ids(
        self, turn_id: str, fact_ids: tuple[str, ...]
    ) -> ConversationTurn:
        with Session(self.engine) as db:
            row = db.get(TurnRow, turn_id)
            if row is None:
                raise NotFoundError(f"Turn {turn_id} was not found")
            if row.response_state not in {
                TurnResponseState.AUDIO_STARTED.value,
                TurnResponseState.DELIVERY_UNCONFIRMED.value,
            }:
                raise InvalidStateError("Grounding requires observed playback")
            row.selected_fact_ids = list(dict.fromkeys(fact_ids))
            db.commit()
            db.refresh(row)
            return self._turn(row)

    def switch_voice_stack_before_first_turn(
        self,
        session_id: str,
        *,
        expected_stack_id: str,
        target_stack_id: str,
        target_stack_version: str,
        target_stack_config: Mapping[str, Any],
        reason: str,
    ) -> ConversationSession:
        with Session(self.engine) as db:
            session = db.get(SessionRow, session_id)
            if session is None:
                raise NotFoundError(f"Session {session_id} was not found")
            if session.voice_stack_id == target_stack_id:
                return self.get_session(session_id)
            if session.voice_stack_id != expected_stack_id:
                raise InvalidStateError("Session voice stack changed concurrently")
            expected_model = str(
                dict(session.voice_stack_config.get("models", {})).get("realtime", "")
            )
            failure = db.scalar(
                select(ExecutionRow)
                .where(
                    ExecutionRow.session_id == session_id,
                    ExecutionRow.operation == "realtime_voice.connect",
                    ExecutionRow.status == ExecutionStatus.FAILED.value,
                    ExecutionRow.model == expected_model,
                )
                .order_by(ExecutionRow.created_at.desc())
                .limit(1)
            )
            if failure is None:
                raise InvalidStateError("A recorded Realtime connection failure is required")
            previous_id, previous_version = session.voice_stack_id, session.voice_stack_version
            previous_config = dict(session.voice_stack_config)
            result = db.connection().execute(
                update(SessionRow)
                .where(
                    SessionRow.id == session_id,
                    SessionRow.voice_stack_id == expected_stack_id,
                    ~select(TurnRow.id).where(TurnRow.session_id == session_id).exists(),
                )
                .values(
                    voice_stack_id=target_stack_id,
                    voice_stack_version=target_stack_version,
                    voice_stack_config=_jsonable(dict(target_stack_config)),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                current = self.get_session(session_id)
                if current.voice_stack_id == target_stack_id:
                    return current
                raise InvalidStateError("Fallback raced with a transcript or stack change")
            db.add(
                VoiceStackTransitionRow(
                    id=new_id(),
                    session_id=session_id,
                    from_stack_id=previous_id,
                    from_stack_version=previous_version,
                    from_stack_config=previous_config,
                    to_stack_id=target_stack_id,
                    to_stack_version=target_stack_version,
                    to_stack_config=_jsonable(dict(target_stack_config)),
                    reason=reason,
                    failure_execution_id=failure.id,
                    created_at=utc_now(),
                )
            )
            db.commit()
        return self.get_session(session_id)

    @staticmethod
    def _audio_row(db: Session, session_id: str, turn_id: str, audio_stream_id: str) -> TurnRow:
        row = db.get(TurnRow, turn_id)
        if row is None:
            raise NotFoundError(f"Turn {turn_id} was not found")
        if row.session_id != session_id or row.audio_stream_id != audio_stream_id:
            raise InvalidStateError("Audio acknowledgement correlation mismatch")
        return row

    @classmethod
    def _validated_ack(
        cls,
        db: Session,
        session_id: str,
        turn_id: str,
        audio_stream_id: str,
        provider_response_id: str | None,
        last_index: int,
        require_last: bool,
    ) -> TurnRow:
        row = cls._audio_row(db, session_id, turn_id, audio_stream_id)
        if row.provider_response_id != provider_response_id:
            raise InvalidStateError("Audio response mismatch")
        if row.expected_audio_chunks is None:
            raise InvalidStateError("Audio has not been fully sent")
        final_index = row.expected_audio_chunks - 1
        if (
            last_index < 0
            or last_index > final_index
            or (require_last and last_index != final_index)
        ):
            raise InvalidStateError("Audio acknowledgement index mismatch")
        return row

    def record_execution(self, execution: ExecutionRecord) -> None:
        with Session(self.engine) as db:
            existing = db.get(ExecutionRow, execution.id)
            if existing is not None:
                if existing.turn_id is None and execution.turn_id is not None:
                    existing.turn_id = execution.turn_id
                    db.commit()
                return
            values = asdict(execution)
            values["status"] = execution.status.value
            values["cost_status"] = execution.cost_status.value
            values["cost_assumptions"] = list(execution.cost_assumptions)
            db.add(ExecutionRow(**values))
            db.commit()

    def record_voice_turn_metric(self, metric: VoiceTurnMetric) -> None:
        with Session(self.engine) as db:
            session = db.get(SessionRow, metric.session_id)
            if session is None:
                raise NotFoundError(f"Session {metric.session_id} was not found")
            turn = db.get(TurnRow, metric.turn_id)
            if turn is None:
                raise NotFoundError(f"Turn {metric.turn_id} was not found")
            if turn.session_id != metric.session_id:
                raise InvalidStateError("Voice metric turn does not belong to its session")
            if (
                metric.voice_stack_id != session.voice_stack_id
                or session.voice_stack_config.get("id") != session.voice_stack_id
            ):
                raise InvalidStateError("Voice metric stack does not match its session")
            if metric.transport.value != session.voice_stack_config.get("transport"):
                raise InvalidStateError("Voice metric transport does not match its session stack")
            values = asdict(metric)
            values["transport"] = metric.transport.value
            values["status"] = metric.status.value
            values["interaction_mode"] = (
                metric.interaction_mode.value if metric.interaction_mode is not None else None
            )
            values["delivery_status"] = metric.delivery_status.value
            values["clock_domains"] = {
                key: ClockDomain(value).value for key, value in metric.clock_domains.items()
            }
            existing = db.get(VoiceTurnMetricRow, metric.id)
            if existing is None:
                db.add(VoiceTurnMetricRow(**values))
            else:
                for key, value in values.items():
                    if key != "id":
                        setattr(existing, key, value)
            db.commit()

    def get_voice_turn_metric(self, turn_id: str) -> VoiceTurnMetric | None:
        with Session(self.engine) as db:
            row = db.scalar(
                select(VoiceTurnMetricRow)
                .where(VoiceTurnMetricRow.turn_id == turn_id)
                .order_by(VoiceTurnMetricRow.created_at.desc())
                .limit(1)
            )
            return self._voice_turn_metric(row) if row is not None else None

    def list_voice_turn_metrics(
        self, session_id: str | None = None
    ) -> tuple[VoiceTurnMetric, ...]:
        with Session(self.engine) as db:
            statement = select(VoiceTurnMetricRow)
            if session_id is not None:
                statement = statement.where(VoiceTurnMetricRow.session_id == session_id)
            rows = db.scalars(
                statement.order_by(VoiceTurnMetricRow.created_at, VoiceTurnMetricRow.id)
            )
            return tuple(self._voice_turn_metric(row) for row in rows)

    def save_patient_opening(self, opening: PatientOpening) -> PatientOpening:
        with Session(self.engine) as db:
            if db.get(SessionRow, opening.session_id) is None:
                raise NotFoundError(f"Session {opening.session_id} was not found")
            row = db.get(PatientOpeningRow, opening.session_id)
            if row is None:
                row = PatientOpeningRow(
                    session_id=opening.session_id,
                    text=opening.text,
                    spoken_text=opening.spoken_text,
                    status=opening.status.value,
                    provider_response_id=opening.provider_response_id,
                    created_at=opening.created_at,
                )
                db.add(row)
            else:
                row.text = opening.text
                row.spoken_text = opening.spoken_text
                row.status = opening.status.value
                row.provider_response_id = opening.provider_response_id
            db.commit()
            db.refresh(row)
            return self._patient_opening(row)

    def record_vocabulary_hint_usage(
        self, session_id: str, hint_id: str, asset_version: str
    ) -> VocabularyHintUsage:
        now = utc_now()
        with Session(self.engine) as db:
            if db.get(SessionRow, session_id) is None:
                raise NotFoundError(f"Session {session_id} was not found")
            row = db.get(VocabularyHintUsageRow, (session_id, hint_id))
            if row is None:
                row = VocabularyHintUsageRow(
                    session_id=session_id,
                    hint_id=hint_id,
                    asset_version=asset_version,
                    usage_count=1,
                    first_used_at=now,
                    last_used_at=now,
                )
                db.add(row)
            else:
                row.usage_count += 1
                row.last_used_at = now
            db.commit()
            db.refresh(row)
            return self._vocabulary_hint_usage(row)

    def save_analysis(
        self,
        session_id: str,
        evaluation: Evaluation,
        metrics: SessionMetrics,
        vocabulary: tuple[VocabularyObservation, ...],
    ) -> None:
        with Session(self.engine) as db:
            db.merge(EvaluationRow(session_id=session_id, payload=_jsonable(evaluation)))
            db.merge(MetricsRow(session_id=session_id, payload=_jsonable(metrics)))
            existing = tuple(
                db.scalars(select(VocabularyRow).where(VocabularyRow.session_id == session_id))
            )
            for existing_item in existing:
                db.delete(existing_item)
            for observation in vocabulary:
                values = asdict(observation)
                values["state"] = observation.state.value
                db.add(VocabularyRow(**values))
            row = db.get(SessionRow, session_id)
            if row is None:
                raise NotFoundError(f"Session {session_id} was not found")
            row.status = SessionStatus.COMPLETED.value
            if row.ended_at is None:
                row.ended_at = utc_now()
            db.commit()

    @staticmethod
    def _turn(row: TurnRow) -> ConversationTurn:
        return ConversationTurn(
            id=row.id,
            session_id=row.session_id,
            sequence=row.sequence,
            user_text=row.user_text,
            patient_text=row.patient_text,
            revealed_fact_ids=tuple(row.revealed_fact_ids),
            selected_fact_ids=tuple(row.selected_fact_ids),
            provider_input_item_id=row.provider_input_item_id,
            provider_response_id=row.provider_response_id,
            interrupted=row.interrupted,
            interruption_audio_end_ms=row.interruption_audio_end_ms,
            provider_response_status=row.provider_response_status,
            response_state=TurnResponseState(row.response_state),
            delivery_status=AudioDeliveryStatus(row.delivery_status),
            audio_stream_id=row.audio_stream_id,
            audio_attempt=row.audio_attempt,
            expected_audio_chunks=row.expected_audio_chunks,
            audio_sent_at=_dt(row.audio_sent_at) if row.audio_sent_at else None,
            audio_started_at=_dt(row.audio_started_at) if row.audio_started_at else None,
            audio_delivered_at=(
                _dt(row.audio_delivered_at) if row.audio_delivered_at is not None else None
            ),
            created_at=_dt(row.created_at),
            normalized_user_text=row.normalized_user_text,
            canonical_response=dict(row.canonical_response) if row.canonical_response else None,
            observed_response_text=row.observed_response_text,
        )

    @staticmethod
    def _voice_stack_transition(row: VoiceStackTransitionRow) -> VoiceStackTransition:
        return VoiceStackTransition(
            id=row.id,
            session_id=row.session_id,
            from_stack_id=row.from_stack_id,
            from_stack_version=row.from_stack_version,
            from_stack_config=dict(row.from_stack_config),
            to_stack_id=row.to_stack_id,
            to_stack_version=row.to_stack_version,
            to_stack_config=dict(row.to_stack_config),
            reason=row.reason,
            failure_execution_id=row.failure_execution_id,
            created_at=_dt(row.created_at),
        )

    @staticmethod
    def _voice_turn_metric(row: VoiceTurnMetricRow) -> VoiceTurnMetric:
        return VoiceTurnMetric(
            id=row.id,
            session_id=row.session_id,
            turn_id=row.turn_id,
            schema_version=row.schema_version,
            trace_id=row.trace_id,
            voice_stack_id=row.voice_stack_id,
            voice_stack_version=row.voice_stack_version,
            transport=VoiceMetricTransport(row.transport),
            models=dict(row.models),
            provider_ids=dict(row.provider_ids),
            case_id=row.case_id,
            case_version=row.case_version,
            case_hash=row.case_hash,
            interaction_mode=(
                InteractionMode(row.interaction_mode) if row.interaction_mode is not None else None
            ),
            prompt_versions=dict(row.prompt_versions),
            prompt_hashes=dict(row.prompt_hashes),
            delivery_status=AudioDeliveryStatus(row.delivery_status),
            application_version=row.application_version,
            clock_domains={key: ClockDomain(value) for key, value in row.clock_domains.items()},
            wall_timestamps_utc=dict(row.wall_timestamps_utc),
            speech_end_to_transcript_final_ms=row.speech_end_to_transcript_final_ms,
            transcript_final_to_llm_first_token_ms=(
                row.transcript_final_to_llm_first_token_ms
            ),
            llm_total_ms=row.llm_total_ms,
            llm_complete_to_tts_first_byte_ms=row.llm_complete_to_tts_first_byte_ms,
            tts_total_ms=row.tts_total_ms,
            speech_end_to_first_audio_sent_ms=row.speech_end_to_first_audio_sent_ms,
            speech_end_to_audio_started_ms=row.speech_end_to_audio_started_ms,
            audio_sent_to_playback_started_ms=row.audio_sent_to_playback_started_ms,
            speech_end_to_transcript_ms=row.speech_end_to_transcript_ms,
            transcript_to_first_token_ms=row.transcript_to_first_token_ms,
            first_token_to_first_audio_ms=row.first_token_to_first_audio_ms,
            speech_end_to_first_audio_ms=row.speech_end_to_first_audio_ms,
            turn_total_ms=row.turn_total_ms,
            interruption_count=row.interruption_count,
            error_count=row.error_count,
            retry_count=row.retry_count,
            status=VoiceTurnMetricStatus(row.status),
            created_at=_dt(row.created_at),
            updated_at=_dt(row.updated_at),
        )

    @staticmethod
    def _grounding_audit(row: GroundingAuditRow) -> GroundingAudit:
        return GroundingAudit(
            id=row.id,
            session_id=row.session_id,
            turn_id=row.turn_id,
            schema_version=row.schema_version,
            prompt_version=row.prompt_version,
            supported_fact_ids=tuple(row.supported_fact_ids),
            unsupported_claims=tuple(row.unsupported_claims),
            severity=row.severity,
            confidence=row.confidence,
            created_at=_dt(row.created_at),
        )

    @staticmethod
    def _vocabulary(row: VocabularyRow) -> VocabularyObservation:
        return VocabularyObservation(
            id=row.id,
            session_id=row.session_id,
            lemma=row.lemma,
            translation=row.translation,
            example=row.example,
            evidence_turn_sequences=tuple(row.evidence_turn_sequences),
            state=VocabularyState(row.state),
            confidence=row.confidence,
        )

    @staticmethod
    def _vocabulary_hint_usage(row: VocabularyHintUsageRow) -> VocabularyHintUsage:
        return VocabularyHintUsage(
            session_id=row.session_id,
            hint_id=row.hint_id,
            asset_version=row.asset_version,
            usage_count=row.usage_count,
            first_used_at=_dt(row.first_used_at),
            last_used_at=_dt(row.last_used_at),
        )

    @staticmethod
    def _patient_opening(row: PatientOpeningRow) -> PatientOpening:
        return PatientOpening(
            session_id=row.session_id,
            text=row.text,
            spoken_text=row.spoken_text,
            status=PatientOpeningStatus(row.status),
            provider_response_id=row.provider_response_id,
            created_at=_dt(row.created_at),
        )

    @staticmethod
    def _evaluation(value: dict[str, Any]) -> Evaluation:
        value = dict(value)
        for key in ("strengths", "priorities", "language_errors"):
            value[key] = tuple(
                EvidenceObservation(
                    text=item["text"] if isinstance(item, dict) else str(item),
                    evidence_turn_sequences=(
                        tuple(item["evidence_turn_sequences"]) if isinstance(item, dict) else ()
                    ),
                )
                for item in value[key]
            )
        for key in ("missed_fact_ids", "criteria"):
            value[key] = tuple(value[key])
        value.setdefault("rubric_version", "unknown")
        value["created_at"] = (
            datetime.fromisoformat(value["created_at"])
            if isinstance(value["created_at"], str)
            else value["created_at"]
        )
        return Evaluation(**value)

    @staticmethod
    def _execution(row: ExecutionRow) -> ExecutionRecord:
        return ExecutionRecord(
            id=row.id,
            session_id=row.session_id,
            operation=row.operation,
            provider=row.provider,
            model=row.model,
            status=ExecutionStatus(row.status),
            prompt_version=row.prompt_version,
            prompt_hash=row.prompt_hash,
            case_version=row.case_version,
            case_hash=row.case_hash,
            latency_ms=row.latency_ms,
            usage=row.usage,
            estimated_cost_usd=row.estimated_cost_usd,
            pricing_version=row.pricing_version,
            cost_status=CostStatus(row.cost_status),
            cost_amount_usd=row.cost_amount_usd,
            cost_units=dict(row.cost_units),
            cost_assumptions=tuple(row.cost_assumptions),
            cost_unknown_reason=row.cost_unknown_reason,
            provider_request_id=row.provider_request_id,
            turn_id=row.turn_id,
            error_code=row.error_code,
            error_message=row.error_message,
            retryable=row.retryable,
            created_at=_dt(row.created_at),
        )
