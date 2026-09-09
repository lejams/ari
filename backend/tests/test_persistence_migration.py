from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
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
    TurnResponseState,
    VoiceMetricTransport,
    VoiceTurnMetric,
    new_id,
)
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository

HEAD_REVISION = "20260906_0009"

HISTORICAL_SCHEMA = """
CREATE TABLE learners (
    id VARCHAR NOT NULL PRIMARY KEY,
    goal JSON NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE sessions (
    id VARCHAR NOT NULL PRIMARY KEY,
    learner_id VARCHAR NOT NULL,
    case_id VARCHAR NOT NULL,
    case_version VARCHAR NOT NULL,
    case_hash VARCHAR NOT NULL,
    goal JSON NOT NULL,
    voice_profile VARCHAR NOT NULL,
    interaction_mode VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    created_at DATETIME NOT NULL,
    call_started_at DATETIME,
    ended_at DATETIME,
    FOREIGN KEY(learner_id) REFERENCES learners (id)
);
CREATE INDEX ix_sessions_learner_id ON sessions (learner_id);
CREATE INDEX ix_sessions_status ON sessions (status);
CREATE TABLE turns (
    id VARCHAR NOT NULL PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    sequence INTEGER NOT NULL,
    user_text TEXT NOT NULL,
    patient_text TEXT NOT NULL,
    revealed_fact_ids JSON NOT NULL,
    provider_input_item_id VARCHAR,
    provider_response_id VARCHAR,
    interrupted BOOLEAN NOT NULL,
    interruption_audio_end_ms INTEGER,
    provider_response_status VARCHAR NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE (session_id, sequence),
    FOREIGN KEY(session_id) REFERENCES sessions (id)
);
CREATE INDEX ix_turns_provider_response_id ON turns (provider_response_id);
CREATE INDEX ix_turns_session_id ON turns (session_id);
CREATE UNIQUE INDEX uq_turn_session_input_item
    ON turns(session_id, provider_input_item_id)
    WHERE provider_input_item_id IS NOT NULL;
CREATE UNIQUE INDEX uq_turn_session_response
    ON turns(session_id, provider_response_id)
    WHERE provider_response_id IS NOT NULL;
CREATE TABLE patient_openings (
    session_id VARCHAR NOT NULL PRIMARY KEY,
    text TEXT NOT NULL,
    spoken_text TEXT,
    status VARCHAR NOT NULL,
    provider_response_id VARCHAR,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions (id)
);
CREATE TABLE grounding_audits (
    id VARCHAR NOT NULL PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    turn_id VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    supported_fact_ids JSON NOT NULL,
    unsupported_claims JSON NOT NULL,
    severity VARCHAR NOT NULL,
    confidence FLOAT NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions (id),
    FOREIGN KEY(turn_id) REFERENCES turns (id)
);
CREATE INDEX ix_grounding_audits_session_id ON grounding_audits (session_id);
CREATE INDEX ix_grounding_audits_turn_id ON grounding_audits (turn_id);
CREATE TABLE evaluations (
    session_id VARCHAR NOT NULL PRIMARY KEY,
    payload JSON NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions (id)
);
CREATE TABLE session_metrics (
    session_id VARCHAR NOT NULL PRIMARY KEY,
    payload JSON NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions (id)
);
CREATE TABLE vocabulary_observations (
    id VARCHAR NOT NULL PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    lemma VARCHAR NOT NULL,
    translation VARCHAR NOT NULL,
    example TEXT NOT NULL,
    evidence_turn_sequences JSON NOT NULL,
    state VARCHAR NOT NULL,
    confidence FLOAT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions (id)
);
CREATE INDEX ix_vocabulary_observations_session_id
    ON vocabulary_observations (session_id);
CREATE TABLE vocabulary_hint_usages (
    session_id VARCHAR NOT NULL,
    hint_id VARCHAR NOT NULL,
    asset_version VARCHAR NOT NULL,
    usage_count INTEGER NOT NULL,
    first_used_at DATETIME NOT NULL,
    last_used_at DATETIME NOT NULL,
    PRIMARY KEY (session_id, hint_id),
    FOREIGN KEY(session_id) REFERENCES sessions (id)
);
CREATE TABLE executions (
    id VARCHAR NOT NULL PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    operation VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    prompt_version VARCHAR,
    prompt_hash VARCHAR,
    case_version VARCHAR NOT NULL,
    case_hash VARCHAR NOT NULL,
    latency_ms INTEGER NOT NULL,
    usage JSON NOT NULL,
    estimated_cost_usd FLOAT,
    pricing_version VARCHAR NOT NULL,
    provider_request_id VARCHAR,
    turn_id VARCHAR,
    error_code VARCHAR,
    error_message TEXT,
    retryable BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions (id)
);
CREATE INDEX ix_executions_session_id ON executions (session_id);
"""


def _database_url(database: Path) -> str:
    return f"sqlite:///{database}"


def _upgrade(database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARI_DATABASE_URL", _database_url(database))
    configuration = Config(PROJECT_ROOT / "alembic.ini")
    command.upgrade(configuration, "head")
    command.upgrade(configuration, "head")


def _create_historical_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(HISTORICAL_SCHEMA)
        connection.execute(
            "INSERT INTO learners (id, goal, created_at) VALUES (?, ?, ?)",
            (
                "legacy-learner",
                '{"target_exam":"FSP","target_cefr":"C1","rubric_version":"v1"}',
                "2026-08-01 09:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO sessions (
                id, learner_id, case_id, case_version, case_hash, goal,
                voice_profile, interaction_mode, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-session",
                "legacy-learner",
                "legacy-case",
                "1.0",
                "legacy-hash",
                '{"target_exam":"FSP","target_cefr":"C1","rubric_version":"v1"}',
                "economy",
                "guided",
                "active",
                "2026-08-01 10:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO turns (
                id, session_id, sequence, user_text, patient_text, revealed_fact_ids,
                interrupted, provider_response_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-turn",
                "legacy-session",
                1,
                "Seit wann?",
                "Seit gestern.",
                '["symptom.onset"]',
                0,
                "completed",
                "2026-08-01 10:01:00",
            ),
        )


def test_migration_upgrades_empty_database_to_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "empty.db"

    _upgrade(database, monkeypatch)

    repository = SqliteSessionRepository(_database_url(database))
    tables = set(inspect(repository.engine).get_table_names())
    assert {
        "alembic_version",
        "learners",
        "sessions",
        "turns",
        "voice_turn_metrics",
        "voice_stack_transitions",
        "profile_credentials",
        "practice_runs",
        "practice_answers",
        "voice_learning_context",
        "voice_start_requests",
    } <= tables
    with repository.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD_REVISION
    from ari.infrastructure.persistence.identity import ProfileCredentials

    learner = repository.create_learner(LearnerProfile(id="migration-owner", goal=LearningGoal()))
    credentials = ProfileCredentials(repository.engine)
    token = credentials.issue(learner.id)
    assert credentials.resolve(token) == learner.id


def test_unified_turn_columns_are_nullable_without_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "unified-turns.db"
    _create_historical_database(database)
    _upgrade(database, monkeypatch)
    repository = SqliteSessionRepository(_database_url(database))
    columns = {item["name"]: item for item in inspect(repository.engine).get_columns("turns")}
    assert columns["normalized_user_text"]["nullable"] is True
    assert columns["canonical_response"]["nullable"] is True
    assert columns["observed_response_text"]["nullable"] is True
    with repository.engine.connect() as connection:
        values = connection.execute(
            text(
                "SELECT normalized_user_text, canonical_response, observed_response_text "
                "FROM turns WHERE id='legacy-turn'"
            )
        ).one()
    assert values == (None, None, None)
def test_migration_preserves_historical_data_and_backfills_unknowns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "historical.db"
    _create_historical_database(database)

    _upgrade(database, monkeypatch)

    repository = SqliteSessionRepository(_database_url(database))
    session = repository.get_session("legacy-session")
    assert session.learning_mode is None
    assert session.learner_id == "legacy-learner"
    assert session.turns[0].patient_text == "Seit gestern."
    assert session.voice_stack_id == "pipeline_economy"
    assert session.voice_stack_version == "1"
    assert session.voice_stack_config == {
        "id": "pipeline_economy",
        "models": {},
        "parameters": {"migration": "historical_schema_without_voice_stack"},
        "provider": "legacy_unknown",
        "transport": "pipeline",
        "version": "1",
    }
    assert session.turns[0].delivery_status is AudioDeliveryStatus.LEGACY_UNKNOWN
    assert session.turns[0].selected_fact_ids == session.turns[0].revealed_fact_ids
    assert session.turns[0].audio_delivered_at is None
    with repository.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM learners")) == 1
        assert connection.scalar(text("SELECT count(*) FROM sessions")) == 1
        assert connection.scalar(text("SELECT count(*) FROM turns")) == 1
        raw_snapshot = connection.scalar(
            text("SELECT voice_stack_config FROM sessions WHERE id = 'legacy-session'")
        )
    assert json.loads(str(raw_snapshot))["provider"] == "legacy_unknown"


def test_delivery_migration_upgrades_from_first_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "from-first-revision.db"
    _create_historical_database(database)
    monkeypatch.setenv("ARI_DATABASE_URL", _database_url(database))
    configuration = Config(PROJECT_ROOT / "alembic.ini")
    command.upgrade(configuration, "20260902_0001")
    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(turns)")]
        original = dict(
            zip(
                columns,
                connection.execute("SELECT * FROM turns WHERE id='legacy-turn'").fetchone(),
                strict=True,
            )
        )
        rows = (
            {
                **original,
                "id": "tts-failed",
                "sequence": 2,
                "delivery_status": "failed",
                "provider_response_status": "completed",
            },
            {
                **original,
                "id": "pending",
                "sequence": 3,
                "delivery_status": "pending",
                "provider_response_status": "completed",
            },
            {
                **original,
                "id": "delivered",
                "sequence": 4,
                "delivery_status": "delivered",
                "provider_response_status": "completed",
            },
        )
        placeholders = ",".join("?" for _ in columns)
        for row in rows:
            connection.execute(
                f"INSERT INTO turns ({','.join(columns)}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )
    command.upgrade(configuration, "head")
    repository = SqliteSessionRepository(_database_url(database))
    columns = {item["name"] for item in inspect(repository.engine).get_columns("turns")}
    assert {"selected_fact_ids", "response_state", "audio_stream_id"} <= columns
    turns = {turn.id: turn for turn in repository.get_session("legacy-session").turns}
    assert turns["tts-failed"].response_state is TurnResponseState.TTS_FAILED
    assert turns["tts-failed"].revealed_fact_ids == ()
    assert turns["pending"].response_state is TurnResponseState.DELIVERY_UNCONFIRMED
    assert turns["pending"].selected_fact_ids == ("symptom.onset",)
    assert turns["pending"].revealed_fact_ids == ()
    assert turns["delivered"].response_state is TurnResponseState.AUDIO_DELIVERED
    assert turns["delivered"].revealed_fact_ids == ("symptom.onset",)
    with repository.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD_REVISION


def test_goal3_migration_preserves_historical_metrics_and_costs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "from-goal2.db"
    monkeypatch.setenv("ARI_DATABASE_URL", _database_url(database))
    configuration = Config(PROJECT_ROOT / "alembic.ini")
    command.upgrade(configuration, "20260903_0002")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO learners VALUES (?, ?, ?)",
            (
                "learner",
                '{"target_exam":"FSP","target_cefr":"C1","rubric_version":"r"}',
                "2026-09-03 10:00:00",
            ),
        )
        stack = json.dumps(
            {
                "id": "pipeline_economy",
                "version": "1",
                "transport": "pipeline",
                "provider": "fake",
                "models": {},
                "parameters": {},
            }
        )
        connection.execute(
            """
            INSERT INTO sessions (
              id, learner_id, case_id, case_version, case_hash, goal, voice_profile,
              interaction_mode, voice_stack_id, voice_stack_version, voice_stack_config,
              status, created_at, call_started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                "session",
                "learner",
                "case",
                "1",
                "case-hash",
                '{"target_exam":"FSP","target_cefr":"C1","rubric_version":"r"}',
                "economy",
                "guided",
                "pipeline_economy",
                "1",
                stack,
                "active",
                "2026-09-03 10:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO turns (
              id, session_id, sequence, user_text, patient_text, revealed_fact_ids,
              selected_fact_ids, provider_input_item_id, provider_response_id, interrupted,
              interruption_audio_end_ms, provider_response_status, response_state,
              delivery_status, audio_stream_id, audio_attempt, expected_audio_chunks,
              audio_sent_at, audio_started_at, audio_delivered_at, created_at
            ) VALUES (?, ?, 1, 'Frage', 'Antwort', '[]', '[]', NULL, 'response', 0,
                      NULL, 'completed', 'audio_delivered', 'delivered', 'stream', 1, 1,
                      NULL, NULL, NULL, ?)
            """,
            ("turn", "session", "2026-09-03 10:00:01"),
        )
        connection.execute(
            """
            INSERT INTO voice_turn_metrics (
              id, session_id, turn_id, voice_stack_id, transport,
              speech_end_to_transcript_ms, transcript_to_first_token_ms,
              first_token_to_first_audio_ms, speech_end_to_first_audio_ms,
              turn_total_ms, status, created_at
            ) VALUES ('metric', 'session', 'turn', 'pipeline_economy', 'pipeline',
                      40, NULL, NULL, 250, 700, 'completed', '2026-09-03 10:00:02')
            """
        )
        connection.execute(
            """
            INSERT INTO executions (
              id, session_id, operation, provider, model, status, prompt_version,
              prompt_hash, case_version, case_hash, latency_ms, usage,
              estimated_cost_usd, pricing_version, provider_request_id, turn_id,
              error_code, error_message, retryable, created_at
            ) VALUES ('execution', 'session', 'patient_response', 'openai', 'legacy-model',
                      'succeeded', NULL, NULL, '1', 'case-hash', 12, '{}', 0.02,
                      'legacy-pricing', NULL, 'turn', NULL, NULL, 0,
                      '2026-09-03 10:00:02')
            """
        )
    command.upgrade(configuration, "head")
    with sqlite3.connect(database) as connection:
        metric = connection.execute(
            """
            SELECT schema_version, trace_id, speech_end_to_transcript_final_ms,
                   speech_end_to_first_audio_sent_ms, turn_total_ms, clock_domains
            FROM voice_turn_metrics WHERE id='metric'
            """
        ).fetchone()
        execution = connection.execute(
            "SELECT cost_status, cost_amount_usd, cost_assumptions FROM executions "
            "WHERE id='execution'"
        ).fetchone()
    assert metric == (
        "voice-turn-metric-legacy-v1",
        "metric",
        40,
        250,
        700,
        "{}",
    )
    assert execution == (
        "estimated",
        0.02,
        '["legacy_estimated_cost_without_structured_units"]',
    )


def test_sqlite_foreign_keys_are_enabled_on_every_repository_connection(
    tmp_path: Path,
) -> None:
    repository = SqliteSessionRepository(_database_url(tmp_path / "foreign-keys.db"))
    repository.initialize_schema()
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
    repository = SqliteSessionRepository(_database_url(tmp_path / "round-trip.db"))
    repository.initialize_schema()
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

    invalid_transport = replace(
        metric,
        id=new_id(),
        transport=VoiceMetricTransport.REALTIME,
    )
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
