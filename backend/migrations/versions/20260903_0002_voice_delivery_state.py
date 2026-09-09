"""Persist response delivery state and explicit voice-stack transitions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0002"
down_revision: str | None = "20260902_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turns",
        sa.Column("selected_fact_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "turns",
        sa.Column(
            "response_state", sa.String(), nullable=False, server_default="delivery_unconfirmed"
        ),
    )
    op.add_column("turns", sa.Column("audio_stream_id", sa.String(), nullable=True))
    op.add_column(
        "turns", sa.Column("audio_attempt", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("turns", sa.Column("expected_audio_chunks", sa.Integer(), nullable=True))
    op.add_column("turns", sa.Column("audio_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("turns", sa.Column("audio_started_at", sa.DateTime(timezone=True), nullable=True))
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE turns SET selected_fact_ids = revealed_fact_ids"))
    bind.execute(
        sa.text("""
        UPDATE turns SET response_state = CASE
          WHEN delivery_status = 'legacy_unknown' THEN 'legacy_unknown'
          WHEN delivery_status = 'delivered' THEN 'audio_delivered'
          WHEN delivery_status = 'failed' AND provider_response_status = 'completed'
               AND patient_text <> '' THEN 'tts_failed'
          WHEN delivery_status = 'failed' THEN 'response_failed'
          ELSE 'delivery_unconfirmed' END
    """)
    )
    bind.execute(
        sa.text("UPDATE turns SET delivery_status='unconfirmed' WHERE delivery_status='pending'")
    )
    bind.execute(
        sa.text(
            "UPDATE turns SET revealed_fact_ids='[]' "
            "WHERE delivery_status NOT IN ('delivered','legacy_unknown')"
        )
    )
    op.create_table(
        "voice_stack_transitions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("from_stack_id", sa.String(), nullable=False),
        sa.Column("from_stack_version", sa.String(), nullable=False),
        sa.Column("from_stack_config", sa.JSON(), nullable=False),
        sa.Column("to_stack_id", sa.String(), nullable=False),
        sa.Column("to_stack_version", sa.String(), nullable=False),
        sa.Column("to_stack_config", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column(
            "failure_execution_id", sa.String(), sa.ForeignKey("executions.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "session_id",
            "from_stack_id",
            "to_stack_id",
            "failure_execution_id",
            name="uq_voice_stack_transition_request",
        ),
    )
    op.create_index(
        "ix_voice_stack_transitions_session_id", "voice_stack_transitions", ["session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_voice_stack_transitions_session_id", table_name="voice_stack_transitions")
    op.drop_table("voice_stack_transitions")
    with op.batch_alter_table("turns") as batch:
        for name in (
            "audio_started_at",
            "audio_sent_at",
            "expected_audio_chunks",
            "audio_attempt",
            "audio_stream_id",
            "response_state",
            "selected_fact_ids",
        ):
            batch.drop_column(name)
