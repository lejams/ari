"""bank of language exercises, their reviews and the learners' answers

Revision ID: 0007_language_exercises
Revises: 0006_learner_tts_provider
Create Date: 2026-11-02

Three new tables, nothing existing is touched, nothing is backfilled:

- `language_exercises`: the bank. The content of a row never changes (UPDATE of a content column
  and DELETE are refused by triggers); the status goes from `active` to `withdrawn` once, with a
  reason and a date, and a withdrawn row is not touched again. A new exercise is a new row.
- `language_exercise_reviews`: append-only; one row per check an exercise went through (blind
  solver, rubric judge, human reading, learner report), with the model and the rubric version.
- `language_drills`: append-only; one row per answer of a learner (idempotent by request id).

Downgrade drops the three tables and the answers and checks they hold: meant for a rollback right
after the upgrade, never in production (restore the predeploy backup instead).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ari.infrastructure.persistence import ddl
from ari.infrastructure.persistence.platform.exercise_rows import EXERCISE_CONTENT_COLUMNS

revision: str = "0007_language_exercises"
down_revision: str | Sequence[str] | None = "0006_learner_tts_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "language_exercises",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("dedup_key", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("skill", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("withdrawn_reason", sa.String(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("level IN ('B1', 'B2', 'C1')", name="ck_language_exercise_level"),
        sa.CheckConstraint(
            "category IN ('gender', 'case', 'verb_form', 'word_order', 'word_choice', 'register')",
            name="ck_language_exercise_category",
        ),
        sa.CheckConstraint(
            "skill IN ('grammar', 'vocabulary', 'register')", name="ck_language_exercise_skill"
        ),
        sa.CheckConstraint(
            "format IN ('cloze', 'correct_sentence', 'register_choice')",
            name="ck_language_exercise_format",
        ),
        sa.CheckConstraint("status IN ('active', 'withdrawn')", name="ck_language_exercise_status"),
        sa.CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL AND withdrawn_reason IS NOT NULL)",
            name="ck_language_exercise_withdrawal",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_language_exercises")),
        sa.UniqueConstraint("content_hash", name=op.f("uq_language_exercises_content_hash")),
        sa.UniqueConstraint("dedup_key", name=op.f("uq_language_exercises_dedup_key")),
        sa.UniqueConstraint(
            "id", "content_hash", name=op.f("uq_language_exercises_id_content_hash")
        ),
    )
    op.create_table(
        "language_exercise_reviews",
        sa.Column("sequence", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("exercise_id", sa.String(), nullable=False),
        sa.Column("reviewer_kind", sa.String(), nullable=False),
        sa.Column("reviewer_name", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reviewer_kind IN ('blind_solver', 'rubric_judge', 'human', 'learner_report')",
            name="ck_language_exercise_reviewer_kind",
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject', 'report')", name="ck_language_exercise_decision"
        ),
        sa.ForeignKeyConstraint(
            ["exercise_id"],
            ["language_exercises.id"],
            name=op.f("fk_language_exercise_reviews_exercise_id_language_exercises"),
        ),
        sa.PrimaryKeyConstraint("sequence", name=op.f("pk_language_exercise_reviews")),
    )
    op.create_index(
        op.f("ix_language_exercise_reviews_exercise_id"),
        "language_exercise_reviews",
        ["exercise_id"],
    )
    op.create_table(
        "language_drills",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("exercise_id", sa.String(), nullable=False),
        sa.Column("exercise_hash", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("chosen_index", sa.Integer(), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["exercise_id"],
            ["language_exercises.id"],
            name=op.f("fk_language_drills_exercise_id_language_exercises"),
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["learners.id"], name=op.f("fk_language_drills_learner_id_learners")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_language_drills")),
        sa.UniqueConstraint("learner_id", "request_id", name="uq_language_drill_request"),
    )
    op.create_index(op.f("ix_language_drills_learner_id"), "language_drills", ["learner_id"])
    op.create_index(op.f("ix_language_drills_exercise_id"), "language_drills", ["exercise_id"])
    ddl.create_immutable_trigger(
        op,
        "language_exercise_content",
        "UPDATE",
        "language_exercises",
        "Immutable language exercise",
        when=ddl.changed(EXERCISE_CONTENT_COLUMNS),
    )
    # A withdrawn exercise is final: not reinstated, not given another reason.
    ddl.create_immutable_trigger(
        op,
        "language_exercise_withdrawn",
        "UPDATE",
        "language_exercises",
        "A withdrawn language exercise stays withdrawn",
        when="OLD.status = 'withdrawn'",
    )
    ddl.create_immutable_trigger(
        op,
        "language_exercise_delete",
        "DELETE",
        "language_exercises",
        "Immutable language exercise",
    )
    ddl.create_append_only_triggers(
        op, ("language_exercise_reviews",), "Immutable language exercise review"
    )
    ddl.create_append_only_triggers(op, ("language_drills",), "Immutable language drill")


def downgrade() -> None:
    op.drop_table("language_drills")
    op.drop_table("language_exercise_reviews")
    op.drop_table("language_exercises")
