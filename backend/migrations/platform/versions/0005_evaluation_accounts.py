"""evaluation accounts

Revision ID: 0005_evaluation_accounts
Revises: 0004_evaluation_runs
Create Date: 2026-10-24

An administrator can mark a learner as an evaluation account (`learners.evaluation_since`, NULL
for everyone else), used by the staging target of `voice-sim` (action 8). Such a learner is
exempt from the exam quota. Each session records at creation whether it was started by an
evaluation account (`sessions.evaluation_account`), so it stays out of the learner metrics, the weekly
programme and the Costs page even if the mark is later removed. Existing rows are learners and
sessions like before: NULL and false. Nothing is backfilled or rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_evaluation_accounts"
down_revision: str | Sequence[str] | None = "0004_evaluation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "learners",
        sa.Column("evaluation_since", sa.DateTime(timezone=True), nullable=True),
    )
    # The server default fills existing rows in one statement and keeps inserts from an image
    # that predates this column valid during a deploy.
    op.add_column(
        "sessions",
        sa.Column("evaluation_account", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("sessions", "evaluation_account")
    op.drop_column("learners", "evaluation_since")
