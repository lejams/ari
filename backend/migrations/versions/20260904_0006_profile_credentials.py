"""Protect local learner profiles without claiming ownership of historical profiles."""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0006"
down_revision = "20260904_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profile_credentials",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("learner_id", sa.String, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.UniqueConstraint("learner_id"),
    )


def downgrade():
    op.drop_table("profile_credentials")
