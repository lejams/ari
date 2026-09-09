"""Additive canonical/observed turn fields for the unified practice lifecycle."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260906_0009"
down_revision = "20260904_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("turns", sa.Column("normalized_user_text", sa.Text(), nullable=True))
    op.add_column(
        "turns",
        sa.Column(
            "canonical_response",
            sa.JSON().with_variant(JSONB(none_as_null=True), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column("turns", sa.Column("observed_response_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("turns", "observed_response_text")
    op.drop_column("turns", "canonical_response")
    op.drop_column("turns", "normalized_user_text")

