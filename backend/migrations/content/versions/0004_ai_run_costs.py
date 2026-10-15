"""ai run costs

Revision ID: 0004_ai_run_costs
Revises: 0003_bundle_drafts
Create Date: 2026-10-15

Every pipeline model call now carries its cost in micro-euros, computed at insert from the
versioned price list. `ai_runs` is append-only: existing rows get their values through the column
defaults (no UPDATE), with `cost_unknown = ["not_computed"]` so their cost stays visibly unknown.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_ai_run_costs"
down_revision: str | Sequence[str] | None = "0003_bundle_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_runs", sa.Column("cost_micro_eur", sa.BigInteger(), nullable=True))
    op.add_column("ai_runs", sa.Column("pricing_version", sa.String(), nullable=True))
    op.add_column(
        "ai_runs",
        sa.Column(
            "cost_unknown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("""'["not_computed"]'::jsonb"""),
        ),
    )
    op.alter_column("ai_runs", "cost_unknown", server_default=None)


def downgrade() -> None:
    op.drop_column("ai_runs", "cost_unknown")
    op.drop_column("ai_runs", "pricing_version")
    op.drop_column("ai_runs", "cost_micro_eur")
