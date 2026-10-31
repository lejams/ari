"""forced training voice per learner

Revision ID: 0006_learner_tts_provider
Revises: 0005_evaluation_accounts
Create Date: 2026-10-31

Training sessions draw their voice (OpenAI or ElevenLabs) at creation and pin it on the session
(`sessions.voice_stack_config`, no schema change). An administrator can force one voice for one
account from the back-office CLI: `learners.tts_provider` ('openai' or 'elevenlabs', NULL for
everyone else = the draw). Nothing is backfilled or rewritten, and an image that predates this
column keeps working during a deploy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_learner_tts_provider"
down_revision: str | Sequence[str] | None = "0005_evaluation_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("learners", sa.Column("tts_provider", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("learners", "tts_provider")
