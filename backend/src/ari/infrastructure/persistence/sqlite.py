from __future__ import annotations

import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
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
    CodeSwitch,
    ConversationSession,
    ConversationTurn,
    Evaluation,
    EvidenceObservation,
    ExecutionRecord,
    ExecutionStatus,
    LearnerDetails,
    LearnerProfile,
    LearningGoal,
    LearningMode,
    PatientResponseKind,
    SessionMetrics,
    SessionStatus,
    TurnResponseState,
    VocabularyObservation,
    VocabularyState,
    utc_now,
)
from ari.infrastructure.persistence.base import Base
from ari.infrastructure.persistence.clinical_rows import (
    ClinicalSessionPinRow,
    ScenarioRow,
    scenario_snapshot,
)
from ari.infrastructure.persistence.identity import ProfileCredentialRow as ProfileCredentialRow
from ari.infrastructure.persistence.lexicon_rows import LexiconEntryRow as LexiconEntryRow
from ari.infrastructure.persistence.placement_rows import (
    PlacementAttemptRow as PlacementAttemptRow,
)
from ari.infrastructure.persistence.practice_rows import PracticeRunRow as PracticeRunRow
from ari.infrastructure.persistence.voice_learning import VoiceLearningRow, VoiceStartRow


class LearnerRow(Base):
    __tablename__ = "learners"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    goal: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), index=True)
    case_id: Mapped[str] = mapped_column(String)
    case_version: Mapped[str] = mapped_column(String)
    case_hash: Mapped[str] = mapped_column(String)
    goal: Mapped[dict[str, Any]] = mapped_column(JSON)
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
    patient_response_kind: Mapped[str] = mapped_column(
        String, default=PatientResponseKind.SOURCES.value
    )


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
    kind: Mapped[str] = mapped_column(String, default="missing")


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
    provider_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _goal(value: dict[str, Any]) -> LearningGoal:
    return LearningGoal(
        target_exam=str(value["target_exam"]),
        target_cefr=CEFRLevel(str(value["target_cefr"])),
        rubric_version=str(value["rubric_version"]),
    )


def _details(value: dict[str, Any] | None) -> LearnerDetails:
    value = value or {}

    def level(name: str) -> CEFRLevel | None:
        raw = value.get(name)
        return CEFRLevel(str(raw)) if raw else None

    def day(name: str) -> date | None:
        raw = value.get(name)
        return date.fromisoformat(str(raw)) if raw else None

    estimated_at = value.get("estimated_at")
    return LearnerDetails(
        declared_level=level("declared_level"),
        level_source=str(value.get("level_source") or "self"),
        certificate_issuer=value.get("certificate_issuer") or None,
        certificate_level=level("certificate_level"),
        certificate_date=day("certificate_date"),
        exam_date=day("exam_date"),
        minutes_per_day=int(value.get("minutes_per_day") or 30),
        land=value.get("land") or None,
        situation=value.get("situation") or None,
        specialty=value.get("specialty") or None,
        estimated_level=level("estimated_level"),
        estimated_at=_dt(datetime.fromisoformat(str(estimated_at))) if estimated_at else None,
        placement_attempt_id=value.get("placement_attempt_id") or None,
    )


def _dt(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
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
                    id=learner.id,
                    goal=_jsonable(learner.goal),
                    created_at=learner.created_at,
                    details=_jsonable(learner.details),
                )
            )
            db.commit()
        return learner

    def get_learner(self, learner_id: str) -> LearnerProfile:
        with Session(self.engine) as db:
            row = db.get(LearnerRow, learner_id)
            if row is None:
                raise NotFoundError(f"Learner {learner_id} was not found")
            return LearnerProfile(
                id=row.id,
                goal=_goal(row.goal),
                created_at=_dt(row.created_at),
                details=_details(row.details),
            )

    def update_learner(self, learner: LearnerProfile) -> LearnerProfile:
        with Session(self.engine) as db:
            row = db.get(LearnerRow, learner.id)
            if row is None:
                raise NotFoundError(f"Learner {learner.id} was not found")
            row.goal = _jsonable(learner.goal)
            row.details = _jsonable(learner.details)
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
                db.execute(
                    update(ScenarioRow)
                    .where(
                        ScenarioRow.id == snapshot.get("scenario_id"),
                        ScenarioRow.version == snapshot.get("scenario_version"),
                    )
                    .values(status=ScenarioRow.status)
                )
                scenario = db.get(
                    ScenarioRow,
                    (
                        snapshot.get("scenario_id"),
                        snapshot.get("scenario_version"),
                    ),
                )
                if (
                    scenario is None
                    or scenario.status != "published"
                    or scenario.phase != "arzt_patient"
                    or snapshot != scenario_snapshot(scenario)
                    or (session.case_id, session.case_version, session.case_hash)
                    != (scenario.case_id, scenario.case_version, scenario.case_hash)
                ):
                    raise InvalidStateError("Publication unavailable or session snapshot mismatch")
            db.add(
                SessionRow(
                    id=session.id,
                    learner_id=session.learner_id,
                    case_id=session.case_id,
                    case_version=session.case_version,
                    case_hash=session.case_hash,
                    goal=_jsonable(session.goal),
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
                db.add(
                    VoiceStartRow(
                        learner_id=session.learner_id,
                        request_id=session.start_request_id,
                        request_hash=session.start_request_hash,
                        session_id=session.id,
                    )
                )
            if session.learning_mode is not None:
                db.flush()
                db.add(VoiceLearningRow(session_id=session.id, mode=session.learning_mode.value))
            if scenario is not None:
                db.flush()
                db.add(
                    ClinicalSessionPinRow(
                        session_id=session.id,
                        scenario_id=scenario.id,
                        scenario_version=scenario.version,
                        scenario_hash=scenario.content_hash,
                    )
                )
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
            evaluation_row = db.get(EvaluationRow, session_id)
            metrics_row = db.get(MetricsRow, session_id)
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
                voice_stack_id=row.voice_stack_id,
                voice_stack_version=row.voice_stack_version,
                voice_stack_config=dict(row.voice_stack_config),
                status=SessionStatus(row.status),
                turns=turns,
                evaluation=self._evaluation(evaluation_row.payload) if evaluation_row else None,
                metrics=SessionMetrics(**metrics_row.payload) if metrics_row else None,
                vocabulary=vocab,
                executions=executions,
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
                    patient_response_kind=turn.patient_response_kind,
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
                        patient_response_kind=turn.patient_response_kind,
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

    def save_selected_response(
        self,
        turn_id: str,
        *,
        patient_text: str,
        selected_fact_ids: tuple[str, ...],
        provider_response_id: str | None,
        provider_response_status: str,
        patient_response_kind: str = PatientResponseKind.SOURCES.value,
    ) -> ConversationTurn:
        with Session(self.engine) as db:
            row = db.get(TurnRow, turn_id)
            if row is None:
                raise NotFoundError(f"Turn {turn_id} was not found")
            failed = provider_response_status not in {"completed", "succeeded"}
            if row.response_state != TurnResponseState.TRANSCRIPT_RESERVED.value:
                same_response = (
                    row.patient_text == patient_text
                    and tuple(row.selected_fact_ids) == selected_fact_ids
                )
                same_provider = row.provider_response_id == provider_response_id or (
                    row.response_state == TurnResponseState.RESPONSE_SELECTED.value
                    and row.provider_response_id is None
                )
                if not (same_response and same_provider):
                    raise InvalidStateError("Turn response has already been selected")
                if row.provider_response_id is None:
                    row.provider_response_id = provider_response_id
                    row.provider_response_status = provider_response_status
                    if failed:
                        row.response_state = TurnResponseState.RESPONSE_FAILED.value
                        row.delivery_status = AudioDeliveryStatus.FAILED.value
                        row.revealed_fact_ids = []
                    db.commit()
                    db.refresh(row)
                return self._turn(row)
            row.patient_text = patient_text
            row.selected_fact_ids = list(dict.fromkeys(selected_fact_ids))
            row.revealed_fact_ids = []
            row.provider_response_id = provider_response_id
            row.provider_response_status = provider_response_status
            row.patient_response_kind = patient_response_kind
            if failed:
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
            db.add(ExecutionRow(**values))
            db.commit()

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
            patient_response_kind=row.patient_response_kind,
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
            kind=row.kind,
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
        value["code_switches"] = tuple(
            CodeSwitch(
                turn=int(item["turn"]),
                fragment=str(item["fragment"]),
                # v3 payloads stored the key under intended_german.
                intended_term=item.get("intended_term", item.get("intended_german")),
            )
            for item in value.get("code_switches", ())
        )
        value["empathy"] = tuple(dict(item) for item in value.get("empathy", ()))
        value["next_actions"] = tuple(dict(item) for item in value.get("next_actions", ()))
        value.setdefault("structure", None)
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
            provider_request_id=row.provider_request_id,
            turn_id=row.turn_id,
            error_code=row.error_code,
            error_message=row.error_message,
            retryable=row.retryable,
            created_at=_dt(row.created_at),
        )
