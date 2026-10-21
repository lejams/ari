"""immutable evaluation runs

Revision ID: 0004_evaluation_runs
Revises: 0003_execution_costs
Create Date: 2026-10-20

Every analysis of a session is now kept: `evaluation_runs` is append-only (UPDATE and DELETE
refused by triggers) and `evaluations` becomes a pointer to the current run, its `payload`
kept as a copy for readers of the former single-row table. Each existing evaluation becomes
run 1 of its session. The run is linked to its execution only when that is unambiguous (the
session has exactly one succeeded `session_evaluation` call, with the same prompt version);
otherwise execution, model and prompt hash stay NULL, unknown rather than guessed.
`executions.model_returned` records the model the provider says answered; NULL for older rows.

Downgrade keeps the current analysis of every session (in `evaluations.payload`) and drops
the earlier runs and `executions.model_returned`: it loses evidence and is meant for a rollback
right after the upgrade, never in production (restore the predeploy backup instead).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ari.infrastructure.persistence import ddl

revision: str = "0004_evaluation_runs"
down_revision: str | Sequence[str] | None = "0003_execution_costs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Idempotent: a session that already has a run is skipped, a pointer already set is kept.
BACKFILL_RUNS = """
INSERT INTO evaluation_runs (
    id, session_id, run_no, payload, schema_version, prompt_version, prompt_hash, model,
    model_returned, rubric_version, execution_id, created_at
)
SELECT
    'backfill:' || e.session_id,
    e.session_id,
    1,
    e.payload,
    COALESCE(e.payload ->> 'schema_version', 'unknown'),
    COALESCE(e.payload ->> 'prompt_version', 'unknown'),
    x.prompt_hash,
    x.model,
    NULL,
    COALESCE(e.payload ->> 'rubric_version', 'unknown'),
    x.id,
    COALESCE((e.payload ->> 'created_at')::timestamptz, s.ended_at, s.created_at)
FROM evaluations AS e
JOIN sessions AS s ON s.id = e.session_id
LEFT JOIN LATERAL (
    SELECT candidate.id, candidate.prompt_hash, candidate.model
    FROM executions AS candidate
    WHERE candidate.session_id = e.session_id
      AND candidate.operation = 'session_evaluation'
      AND candidate.status = 'succeeded'
      AND candidate.prompt_version IS NOT DISTINCT FROM e.payload ->> 'prompt_version'
      AND (
          SELECT count(*)
          FROM executions AS other
          WHERE other.session_id = e.session_id
            AND other.operation = 'session_evaluation'
            AND other.status = 'succeeded'
      ) = 1
) AS x ON true
WHERE NOT EXISTS (SELECT 1 FROM evaluation_runs AS r WHERE r.session_id = e.session_id)
"""
BACKFILL_POINTERS = """
UPDATE evaluations AS e
SET current_run_id = r.id
FROM evaluation_runs AS r
WHERE r.session_id = e.session_id AND r.run_no = 1 AND e.current_run_id IS NULL
"""


def upgrade() -> None:
    op.add_column("executions", sa.Column("model_returned", sa.String(), nullable=True))
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("prompt_hash", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("model_returned", sa.String(), nullable=True),
        sa.Column("rubric_version", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("run_no >= 1", name="ck_evaluation_run_no_positive"),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["executions.id"],
            name=op.f("fk_evaluation_runs_execution_id_executions"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name=op.f("fk_evaluation_runs_session_id_sessions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
        sa.UniqueConstraint("session_id", "id", name=op.f("uq_evaluation_runs_session_id_id")),
        sa.UniqueConstraint(
            "session_id", "run_no", name=op.f("uq_evaluation_runs_session_id_run_no")
        ),
    )
    # Before the backfill, which only inserts.
    ddl.create_append_only_triggers(op, ("evaluation_runs",), "Immutable evaluation run")
    op.add_column("evaluations", sa.Column("current_run_id", sa.String(), nullable=True))
    op.execute(BACKFILL_RUNS)
    op.execute(BACKFILL_POINTERS)
    op.alter_column("evaluations", "current_run_id", existing_type=sa.String(), nullable=False)
    op.create_foreign_key(
        "fk_evaluations_current_run",
        "evaluations",
        "evaluation_runs",
        ["session_id", "current_run_id"],
        ["session_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_evaluations_current_run", "evaluations", type_="foreignkey")
    op.drop_column("evaluations", "current_run_id")
    op.drop_table("evaluation_runs")
    op.drop_column("executions", "model_returned")
