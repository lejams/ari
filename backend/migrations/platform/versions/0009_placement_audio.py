"""recorded audio of the placement listenings

Revision ID: 0009_placement_audio
Revises: 0008_language_drill_runs
Create Date: 2026-11-03

The audio of a listening item is recorded once, when a set is prepared, instead of being
synthesised for the first learner who reaches it. One row per set hash, item and voice, written
once and never changed; the latest row of an item is the one played. A new table only: nothing
existing is touched, and an image that predates it keeps synthesising on demand.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ari.infrastructure.persistence import ddl

revision: str = "0009_placement_audio"
down_revision: str | Sequence[str] | None = "0008_language_drill_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "placement_audio",
        sa.Column("set_hash", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("voice", sa.String(), nullable=False),
        sa.Column("set_id", sa.String(), nullable=False),
        sa.Column("set_version", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("audio_sha256", sa.String(64), nullable=False),
        sa.Column("audio", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id", "set_version", "set_hash"],
            ["placement_sets.id", "placement_sets.version", "placement_sets.content_hash"],
        ),
        sa.PrimaryKeyConstraint("set_hash", "item_id", "voice"),
    )
    ddl.create_append_only_triggers(op, ("placement_audio",), "Immutable placement audio")


def downgrade() -> None:
    op.drop_table("placement_audio")
