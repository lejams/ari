"""Stabilize voice stack and audio delivery persistence.

Revision ID: 20260902_0001
Revises:
Create Date: 2026-09-02
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_STACK_ID = "pipeline_economy"
LEGACY_STACK_VERSION = "1"
LEGACY_STACK_CONFIG = json.dumps(
    {
        "id": LEGACY_STACK_ID,
        "models": {},
        "parameters": {"migration": "historical_schema_without_voice_stack"},
        "provider": "legacy_unknown",
        "transport": "pipeline",
        "version": LEGACY_STACK_VERSION,
    },
    sort_keys=True,
    separators=(",", ":"),
)


def _json_default(value: str) -> sa.TextClause:
    return sa.text("'" + value.replace("'", "''") + "'")


def _create_current_schema() -> None:
    op.create_table(
        "learners",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("goal", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("case_version", sa.String(), nullable=False),
        sa.Column("case_hash", sa.String(), nullable=False),
        sa.Column("goal", sa.JSON(), nullable=False),
        sa.Column("voice_profile", sa.String(), nullable=False),
        sa.Column("interaction_mode", sa.String(), nullable=False),
        sa.Column(
            "voice_stack_id",
            sa.String(),
            nullable=False,
            server_default=LEGACY_STACK_ID,
        ),
        sa.Column(
            "voice_stack_version",
            sa.String(),
            nullable=False,
            server_default=LEGACY_STACK_VERSION,
        ),
        sa.Column(
            "voice_stack_config",
            sa.JSON(),
            nullable=False,
            server_default=_json_default(LEGACY_STACK_CONFIG),
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("call_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_learner_id", "sessions", ["learner_id"])
    op.create_index("ix_sessions_status", "sessions", ["status"])
    op.create_table(
        "turns",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("user_text", sa.Text(), nullable=False),
        sa.Column("patient_text", sa.Text(), nullable=False),
        sa.Column("revealed_fact_ids", sa.JSON(), nullable=False),
        sa.Column("provider_input_item_id", sa.String(), nullable=True),
        sa.Column("provider_response_id", sa.String(), nullable=True),
        sa.Column("interrupted", sa.Boolean(), nullable=False),
        sa.Column("interruption_audio_end_ms", sa.Integer(), nullable=True),
        sa.Column("provider_response_status", sa.String(), nullable=False),
        sa.Column(
            "delivery_status",
            sa.String(),
            nullable=False,
            server_default="legacy_unknown",
        ),
        sa.Column("audio_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sequence"),
        sa.UniqueConstraint(
            "session_id", "provider_input_item_id", name="uq_turn_session_input_item"
        ),
        sa.UniqueConstraint("session_id", "provider_response_id", name="uq_turn_session_response"),
    )
    op.create_index("ix_turns_provider_response_id", "turns", ["provider_response_id"])
    op.create_index("ix_turns_session_id", "turns", ["session_id"])
    op.create_table(
        "patient_openings",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("spoken_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("provider_response_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_table(
        "grounding_audits",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("supported_fact_ids", sa.JSON(), nullable=False),
        sa.Column("unsupported_claims", sa.JSON(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_grounding_audits_session_id", "grounding_audits", ["session_id"])
    op.create_index("ix_grounding_audits_turn_id", "grounding_audits", ["turn_id"])
    op.create_table(
        "evaluations",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_table(
        "session_metrics",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_table(
        "vocabulary_observations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("lemma", sa.String(), nullable=False),
        sa.Column("translation", sa.String(), nullable=False),
        sa.Column("example", sa.Text(), nullable=False),
        sa.Column("evidence_turn_sequences", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vocabulary_observations_session_id", "vocabulary_observations", ["session_id"]
    )
    op.create_table(
        "vocabulary_hint_usages",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("hint_id", sa.String(), nullable=False),
        sa.Column("asset_version", sa.String(), nullable=False),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.Column("first_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("session_id", "hint_id"),
    )
    op.create_table(
        "executions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("prompt_hash", sa.String(), nullable=True),
        sa.Column("case_version", sa.String(), nullable=False),
        sa.Column("case_hash", sa.String(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("pricing_version", sa.String(), nullable=False),
        sa.Column("provider_request_id", sa.String(), nullable=True),
        sa.Column("turn_id", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_executions_session_id", "executions", ["session_id"])
    _create_voice_turn_metrics()


def _create_voice_turn_metrics() -> None:
    op.create_table(
        "voice_turn_metrics",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("voice_stack_id", sa.String(), nullable=False),
        sa.Column("transport", sa.String(), nullable=False),
        sa.Column("speech_end_to_transcript_ms", sa.Integer(), nullable=True),
        sa.Column("transcript_to_first_token_ms", sa.Integer(), nullable=True),
        sa.Column("first_token_to_first_audio_ms", sa.Integer(), nullable=True),
        sa.Column("speech_end_to_first_audio_ms", sa.Integer(), nullable=True),
        sa.Column("turn_total_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"]),
        sa.CheckConstraint(
            "transport IN ('realtime', 'pipeline')",
            name="ck_voice_turn_metrics_transport",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_voice_turn_metrics_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_voice_turn_metrics_session_id", "voice_turn_metrics", ["session_id"])
    op.create_index("ix_voice_turn_metrics_turn_id", "voice_turn_metrics", ["turn_id"])


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    if not tables:
        _create_current_schema()
        return

    session_columns = {column["name"] for column in inspector.get_columns("sessions")}
    if "voice_stack_id" not in session_columns:
        op.add_column(
            "sessions",
            sa.Column(
                "voice_stack_id",
                sa.String(),
                nullable=False,
                server_default=LEGACY_STACK_ID,
            ),
        )
    if "voice_stack_version" not in session_columns:
        op.add_column(
            "sessions",
            sa.Column(
                "voice_stack_version",
                sa.String(),
                nullable=False,
                server_default=LEGACY_STACK_VERSION,
            ),
        )
    if "voice_stack_config" not in session_columns:
        op.add_column(
            "sessions",
            sa.Column(
                "voice_stack_config",
                sa.JSON(),
                nullable=False,
                server_default=_json_default(LEGACY_STACK_CONFIG),
            ),
        )

    turn_columns = {column["name"] for column in inspector.get_columns("turns")}
    if "delivery_status" not in turn_columns:
        op.add_column(
            "turns",
            sa.Column(
                "delivery_status",
                sa.String(),
                nullable=False,
                server_default="legacy_unknown",
            ),
        )
    if "audio_delivered_at" not in turn_columns:
        op.add_column(
            "turns",
            sa.Column("audio_delivered_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "voice_turn_metrics" not in tables:
        _create_voice_turn_metrics()


def downgrade() -> None:
    op.drop_index("ix_voice_turn_metrics_turn_id", table_name="voice_turn_metrics")
    op.drop_index("ix_voice_turn_metrics_session_id", table_name="voice_turn_metrics")
    op.drop_table("voice_turn_metrics")
    with op.batch_alter_table("turns") as batch_op:
        batch_op.drop_column("audio_delivered_at")
        batch_op.drop_column("delivery_status")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("voice_stack_config")
        batch_op.drop_column("voice_stack_version")
        batch_op.drop_column("voice_stack_id")
