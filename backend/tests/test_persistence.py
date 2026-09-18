"""The single Alembic revision creates the schema; the repository enforces integrity on it."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from ari.domain.models import (
    AudioDeliveryStatus,
    ConversationSession,
    ConversationTurn,
    LearnerProfile,
    LearningGoal,
    new_id,
)
from ari.infrastructure.persistence.platform.identity import ProfileCredentials
from ari.infrastructure.persistence.platform.repository import SqlSessionRepository
from ari.infrastructure.persistence.platform.schema import alembic_config


def test_migration_creates_schema_matching_models(database_url: str) -> None:
    command.check(alembic_config(database_url))
    repository = SqlSessionRepository(database_url)
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


def test_foreign_keys_are_enforced(database_url: str) -> None:
    repository = SqlSessionRepository(database_url)
    invalid = ConversationSession(
        id=new_id(),
        learner_id="missing-learner",
        case_id="case",
        case_version="1",
        case_hash="hash",
        goal=LearningGoal(),
    )
    with pytest.raises(IntegrityError):
        repository.create_session(invalid)


def test_session_stack_and_delivery_round_trip(database_url: str) -> None:
    repository = SqlSessionRepository(database_url)
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
    restored = repository.get_session(session.id)
    assert restored.voice_stack_id == session.voice_stack_id
    assert restored.voice_stack_version == session.voice_stack_version
    assert restored.voice_stack_config == snapshot
    assert restored.turns[0].delivery_status is AudioDeliveryStatus.DELIVERED
    assert restored.turns[0].audio_delivered_at is not None
    assert restored.turns[0].revealed_fact_ids == ("fact",)
