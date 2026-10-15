"""execution costs and attribution

Revision ID: 0003_execution_costs
Revises: 0002_learner_accounts
Create Date: 2026-10-15

Every model call now carries its cost in micro-euros, computed at insert from the versioned price
list, and the learner and attempt it was made for. Placement calls, which have no voice session,
are recorded too: `session_id` becomes nullable and `attempt_ref` says "placement:<attempt id>".
Existing rows are attributed from their session and marked `not_computed`: their cost stays
unknown rather than guessed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_execution_costs"
down_revision: str | Sequence[str] | None = "0002_learner_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("executions", "session_id", existing_type=sa.String(), nullable=True)
    op.add_column("executions", sa.Column("learner_id", sa.String(), nullable=True))
    op.add_column("executions", sa.Column("attempt_ref", sa.String(), nullable=True))
    op.add_column("executions", sa.Column("cost_micro_eur", sa.BigInteger(), nullable=True))
    op.add_column("executions", sa.Column("pricing_version", sa.String(), nullable=True))
    op.add_column(
        "executions",
        sa.Column(
            "cost_unknown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("""'["not_computed"]'::jsonb"""),
        ),
    )
    op.alter_column("executions", "cost_unknown", server_default=None)
    op.create_foreign_key(
        op.f("fk_executions_learner_id_learners"),
        "executions",
        "learners",
        ["learner_id"],
        ["id"],
    )
    op.create_index(op.f("ix_executions_learner_id"), "executions", ["learner_id"], unique=False)
    op.create_index(op.f("ix_executions_attempt_ref"), "executions", ["attempt_ref"], unique=False)
    op.execute(
        """
        UPDATE executions AS e
        SET learner_id = s.learner_id, attempt_ref = 'session:' || e.session_id
        FROM sessions AS s
        WHERE s.id = e.session_id
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM executions WHERE session_id IS NULL")
    op.drop_index(op.f("ix_executions_attempt_ref"), table_name="executions")
    op.drop_index(op.f("ix_executions_learner_id"), table_name="executions")
    op.drop_constraint(op.f("fk_executions_learner_id_learners"), "executions", type_="foreignkey")
    op.drop_column("executions", "cost_unknown")
    op.drop_column("executions", "pricing_version")
    op.drop_column("executions", "cost_micro_eur")
    op.drop_column("executions", "attempt_ref")
    op.drop_column("executions", "learner_id")
    op.alter_column("executions", "session_id", existing_type=sa.String(), nullable=False)
