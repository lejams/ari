"""remediation drills on the learner's own language errors

Revision ID: 0008_language_drill_runs
Revises: 0007_language_exercises
Create Date: 2026-11-02

`language_drill_runs` holds one "Votre phrase" drill per row (`language-drill-v1`): the items are the
learner's own quoted errors with their corrections, frozen at creation, and the answers are
appended under a row lock until the drill is completed; a partial unique index keeps one
unfinished drill per learner, so two tabs cannot start two. It adds a table and touches nothing that
exists, so an image that predates it keeps working during a deploy; nothing is backfilled. The
downgrade drops the table and with it the record of which drills were done, but no evaluation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_language_drill_runs"
down_revision: str | Sequence[str] | None = "0007_language_exercises"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "language_drill_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("focus_category", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active','completed')", name="ck_language_drill_status"),
        sa.CheckConstraint(
            "(status = 'completed') = (ended_at IS NOT NULL)", name="ck_language_drill_ended"
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_language_drill_runs_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_language_drill_runs")),
        sa.UniqueConstraint("learner_id", "request_id", name="uq_language_drill_start_request"),
    )
    op.create_index(
        "uq_language_drill_one_active",
        "language_drill_runs",
        ["learner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_language_drill_runs_learner_id"),
        "language_drill_runs",
        ["learner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_language_drill_one_active",
        table_name="language_drill_runs",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(op.f("ix_language_drill_runs_learner_id"), table_name="language_drill_runs")
    op.drop_table("language_drill_runs")
