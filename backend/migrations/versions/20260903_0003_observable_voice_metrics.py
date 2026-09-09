"""Version voice telemetry and structured cost precision.

Revision ID: 20260903_0003
Revises: 20260903_0002
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0003"
down_revision: str | None = "20260903_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_default(value: str) -> sa.TextClause:
    return sa.text(f"'{value}'")


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("cost_status", sa.String(), nullable=False, server_default="unknown"),
    )
    op.add_column("executions", sa.Column("cost_amount_usd", sa.Float(), nullable=True))
    op.add_column(
        "executions",
        sa.Column("cost_units", sa.JSON(), nullable=False, server_default=_json_default("{}")),
    )
    op.add_column(
        "executions",
        sa.Column(
            "cost_assumptions", sa.JSON(), nullable=False, server_default=_json_default("[]")
        ),
    )
    op.add_column("executions", sa.Column("cost_unknown_reason", sa.Text(), nullable=True))
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE executions
            SET cost_status = CASE
                    WHEN estimated_cost_usd IS NULL THEN 'unknown'
                    ELSE 'estimated'
                END,
                cost_amount_usd = estimated_cost_usd,
                cost_assumptions = CASE
                    WHEN estimated_cost_usd IS NULL THEN '[]'
                    ELSE '["legacy_estimated_cost_without_structured_units"]'
                END,
                cost_unknown_reason = CASE
                    WHEN estimated_cost_usd IS NULL
                    THEN 'Historical execution has no structured cost'
                    ELSE NULL
                END
            """
        )
    )

    metric_columns: tuple[sa.Column[object], ...] = (
        sa.Column(
            "schema_version",
            sa.String(),
            nullable=False,
            server_default="voice-turn-metric-legacy-v1",
        ),
        sa.Column("trace_id", sa.String(), nullable=False, server_default=""),
        sa.Column("voice_stack_version", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("models", sa.JSON(), nullable=False, server_default=_json_default("{}")),
        sa.Column("provider_ids", sa.JSON(), nullable=False, server_default=_json_default("{}")),
        sa.Column("case_id", sa.String(), nullable=True),
        sa.Column("case_version", sa.String(), nullable=True),
        sa.Column("case_hash", sa.String(), nullable=True),
        sa.Column("interaction_mode", sa.String(), nullable=True),
        sa.Column("prompt_versions", sa.JSON(), nullable=False, server_default=_json_default("{}")),
        sa.Column("prompt_hashes", sa.JSON(), nullable=False, server_default=_json_default("{}")),
        sa.Column("delivery_status", sa.String(), nullable=False, server_default="legacy_unknown"),
        sa.Column("application_version", sa.String(), nullable=True),
        sa.Column("clock_domains", sa.JSON(), nullable=False, server_default=_json_default("{}")),
        sa.Column(
            "wall_timestamps_utc", sa.JSON(), nullable=False, server_default=_json_default("{}")
        ),
        sa.Column("speech_end_to_transcript_final_ms", sa.Integer(), nullable=True),
        sa.Column("transcript_final_to_llm_first_token_ms", sa.Integer(), nullable=True),
        sa.Column("llm_total_ms", sa.Integer(), nullable=True),
        sa.Column("llm_complete_to_tts_first_byte_ms", sa.Integer(), nullable=True),
        sa.Column("tts_total_ms", sa.Integer(), nullable=True),
        sa.Column("speech_end_to_first_audio_sent_ms", sa.Integer(), nullable=True),
        sa.Column("speech_end_to_audio_started_ms", sa.Integer(), nullable=True),
        sa.Column("audio_sent_to_playback_started_ms", sa.Integer(), nullable=True),
        sa.Column("interruption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("'1970-01-01 00:00:00'"),
        ),
    )
    for column in metric_columns:
        op.add_column("voice_turn_metrics", column)
    op.create_index("ix_voice_turn_metrics_trace_id", "voice_turn_metrics", ["trace_id"])

    bind.execute(sa.text("UPDATE voice_turn_metrics SET trace_id = id, updated_at = created_at"))
    bind.execute(
        sa.text(
            """
            UPDATE voice_turn_metrics
            SET speech_end_to_transcript_final_ms = speech_end_to_transcript_ms,
                transcript_final_to_llm_first_token_ms = transcript_to_first_token_ms,
                speech_end_to_first_audio_sent_ms = speech_end_to_first_audio_ms,
                voice_stack_version = COALESCE(
                    (SELECT voice_stack_version FROM sessions
                     WHERE sessions.id = voice_turn_metrics.session_id),
                    'unknown'
                ),
                case_id = (SELECT case_id FROM sessions
                           WHERE sessions.id = voice_turn_metrics.session_id),
                case_version = (SELECT case_version FROM sessions
                                WHERE sessions.id = voice_turn_metrics.session_id),
                case_hash = (SELECT case_hash FROM sessions
                             WHERE sessions.id = voice_turn_metrics.session_id),
                interaction_mode = (SELECT interaction_mode FROM sessions
                                    WHERE sessions.id = voice_turn_metrics.session_id),
                delivery_status = COALESCE(
                    (SELECT delivery_status FROM turns
                     WHERE turns.id = voice_turn_metrics.turn_id),
                    'legacy_unknown'
                )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_voice_turn_metrics_trace_id", table_name="voice_turn_metrics")
    with op.batch_alter_table("voice_turn_metrics") as batch:
        for name in (
            "updated_at",
            "retry_count",
            "error_count",
            "interruption_count",
            "audio_sent_to_playback_started_ms",
            "speech_end_to_audio_started_ms",
            "speech_end_to_first_audio_sent_ms",
            "tts_total_ms",
            "llm_complete_to_tts_first_byte_ms",
            "llm_total_ms",
            "transcript_final_to_llm_first_token_ms",
            "speech_end_to_transcript_final_ms",
            "wall_timestamps_utc",
            "clock_domains",
            "application_version",
            "delivery_status",
            "prompt_hashes",
            "prompt_versions",
            "interaction_mode",
            "case_hash",
            "case_version",
            "case_id",
            "provider_ids",
            "models",
            "voice_stack_version",
            "trace_id",
            "schema_version",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("executions") as batch:
        for name in (
            "cost_unknown_reason",
            "cost_assumptions",
            "cost_units",
            "cost_amount_usd",
            "cost_status",
        ):
            batch.drop_column(name)
