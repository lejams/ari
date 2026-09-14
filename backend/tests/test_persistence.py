"""The single Alembic revision creates the schema; the repository enforces integrity on it."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from conftest import migrated_database_url
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from ari.config import PROJECT_ROOT
from ari.domain.errors import InvalidStateError
from ari.domain.models import (
    AudioDeliveryStatus,
    ClockDomain,
    ConversationSession,
    ConversationTurn,
    LearnerProfile,
    LearningGoal,
    VoiceMetricTransport,
    VoiceTurnMetric,
    new_id,
)
from ari.infrastructure.persistence.identity import ProfileCredentials
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository


def test_migration_creates_schema_matching_models(tmp_path: Path) -> None:
    url = migrated_database_url(tmp_path / "empty.db")
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    command.check(config)
    repository = SqliteSessionRepository(url)
    tables = set(inspect(repository.engine).get_table_names())
    assert {
        "alembic_version",
        "learners",
        "sessions",
        "turns",
        "profile_credentials",
        "practice_runs",
        "practice_answers",
        "clinical_scenarios",
    } <= tables
    learner = repository.create_learner(LearnerProfile(id="migration-owner", goal=LearningGoal()))
    credentials = ProfileCredentials(repository.engine)
    assert credentials.resolve(credentials.issue(learner.id)) == learner.id


def test_sqlite_foreign_keys_are_enabled_on_every_repository_connection(tmp_path: Path) -> None:
    repository = SqliteSessionRepository(migrated_database_url(tmp_path / "foreign-keys.db"))
    invalid = ConversationSession(
        id=new_id(),
        learner_id="missing-learner",
        case_id="case",
        case_version="1",
        case_hash="hash",
        goal=LearningGoal(),
    )
    with repository.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    with pytest.raises(IntegrityError):
        repository.create_session(invalid)


def test_session_stack_delivery_and_voice_metric_round_trip(tmp_path: Path) -> None:
    repository = SqliteSessionRepository(migrated_database_url(tmp_path / "round-trip.db"))
    learner = LearnerProfile(id=new_id(), goal=LearningGoal())
    repository.create_learner(learner)
    snapshot = {
        "id": "pipeline_economy",
        "version": "1",
        "transport": "pipeline",
        "provider": "fake",
        "models": {"stt": "gpt-transcribe", "llm": "luna", "tts": "tts"},
        "parameters": {"sample_rate": 24_000},
    }
    session = ConversationSession(
        id=new_id(),
        learner_id=learner.id,
        case_id="case",
        case_version="1",
        case_hash="hash",
        goal=learner.goal,
        voice_stack_id="pipeline_economy",
        voice_stack_version="1",
        voice_stack_config=snapshot,
    )
    repository.create_session(session)
    pending_turn = ConversationTurn(
        id=new_id(),
        session_id=session.id,
        sequence=1,
        user_text="Frage",
        patient_text="Antwort",
        revealed_fact_ids=(),
    )
    repository.append_turn(pending_turn)
    assert (
        repository.get_session(session.id).turns[0].delivery_status is AudioDeliveryStatus.PENDING
    )
    selected = repository.save_selected_response(
        pending_turn.id,
        patient_text="Antwort",
        selected_fact_ids=("fact",),
        provider_response_id="response",
        provider_response_status="completed",
    )
    streamed = repository.begin_audio_stream(session.id, selected.id, "stream")
    repository.mark_audio_sent(session.id, streamed.id, "stream", 1)
    repository.confirm_audio_started(
        session.id, streamed.id, "stream", provider_response_id="response", last_index=0
    )
    repository.confirm_audio_delivered(
        session.id, streamed.id, "stream", provider_response_id="response", last_index=0
    )
    metric = VoiceTurnMetric(
        id=new_id(),
        trace_id="trace",
        session_id=session.id,
        turn_id=pending_turn.id,
        voice_stack_id=session.voice_stack_id,
        voice_stack_version=session.voice_stack_version,
        transport=VoiceMetricTransport.PIPELINE,
        models=snapshot["models"],
        delivery_status=AudioDeliveryStatus.DELIVERED,
        speech_end_to_first_audio_sent_ms=240,
        turn_total_ms=810,
        clock_domains={
            "speech_end_to_first_audio_sent_ms": ClockDomain.SERVER,
            "turn_total_ms": ClockDomain.SERVER,
        },
    )
    repository.record_voice_turn_metric(metric)
    repository.record_voice_turn_metric(metric)

    restored = repository.get_session(session.id)
    restored_metrics = repository.list_voice_turn_metrics(session.id)
    assert restored.voice_stack_id == session.voice_stack_id
    assert restored.voice_stack_version == session.voice_stack_version
    assert restored.voice_stack_config == snapshot
    assert restored.turns[0].delivery_status is AudioDeliveryStatus.DELIVERED
    assert restored.turns[0].audio_delivered_at is not None
    assert restored.turns[0].revealed_fact_ids == ("fact",)
    assert restored_metrics == (metric,)

    other_session = replace(session, id=new_id())
    repository.create_session(other_session)
    invalid_pair = replace(metric, id=new_id(), session_id=other_session.id)
    with pytest.raises(InvalidStateError, match="does not belong"):
        repository.record_voice_turn_metric(invalid_pair)

    invalid_stack = replace(metric, id=new_id(), voice_stack_id="realtime_quality")
    with pytest.raises(InvalidStateError, match="stack does not match"):
        repository.record_voice_turn_metric(invalid_stack)

    invalid_transport = replace(metric, id=new_id(), transport=VoiceMetricTransport.REALTIME)
    with pytest.raises(InvalidStateError, match="transport does not match"):
        repository.record_voice_turn_metric(invalid_transport)

    with pytest.raises(ValueError):
        VoiceTurnMetric(
            id=new_id(),
            session_id=session.id,
            turn_id=pending_turn.id,
            voice_stack_id=session.voice_stack_id,
            transport="telepathy",  # type: ignore[arg-type]
        )
