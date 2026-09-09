"""Pinned practice attempts/responses, independent of voice and private case intake."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260904_0007"
down_revision = "20260904_0006"
branch_labels = None
depends_on = None
JSON = sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade():
    op.create_table(
        "practice_runs",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("learner_id", sa.String, nullable=False),
        sa.Column("request_id", sa.String, nullable=False),
        sa.Column("mode", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", JSON, nullable=False),
        sa.Column("feedback", JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.UniqueConstraint("learner_id", "request_id", name="uq_practice_start_request"),
        sa.CheckConstraint("mode IN ('training','exam')", name="ck_practice_mode"),
        sa.CheckConstraint("status IN ('active','paused','completed')", name="ck_practice_status"),
        sa.CheckConstraint(
            "(status = 'completed' AND feedback IS NOT NULL AND ended_at IS NOT NULL) OR "
            "(status != 'completed' AND feedback IS NULL AND ended_at IS NULL)",
            name="ck_practice_feedback_state",
        ),
    )
    op.create_table(
        "practice_answers",
        sa.Column("run_id", sa.String, primary_key=True),
        sa.Column("question_id", sa.String, primary_key=True),
        sa.Column("event_id", sa.String, nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["practice_runs.id"]),
        sa.UniqueConstraint("run_id", "event_id", name="uq_practice_answer_event"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_practice_answer_sequence"),
    )
    columns = ("id", "learner_id", "request_id", "mode", "content_hash", "content", "created_at")
    if op.get_bind().dialect.name == "sqlite":
        condition = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in columns)
        op.execute(
            "CREATE TRIGGER practice_run_immutable BEFORE UPDATE ON practice_runs WHEN "
            f"{condition} OR (OLD.status = 'completed' AND (NEW.status IS NOT OLD.status OR "
            "NEW.feedback IS NOT OLD.feedback OR NEW.ended_at IS NOT OLD.ended_at)) "
            "BEGIN SELECT RAISE(ABORT, 'Immutable practice content/result'); END"
        )
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER practice_answers_no_{action.lower()} BEFORE {action} "
                "ON practice_answers BEGIN SELECT RAISE(ABORT, 'Immutable answer'); END"
            )
        op.execute(
            "CREATE TRIGGER practice_answer_active BEFORE INSERT ON practice_answers "
            "WHEN (SELECT status FROM practice_runs WHERE id=NEW.run_id) != 'active' "
            "BEGIN SELECT RAISE(ABORT, 'Practice is not active'); END"
        )
    elif op.get_bind().dialect.name == "postgresql":
        condition = " OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in columns)
        op.execute(
            "CREATE FUNCTION ari_practice_run_immutable() RETURNS trigger AS $$ BEGIN "
            f"IF {condition} OR (OLD.status='completed' AND (NEW.status IS DISTINCT FROM "
            "OLD.status OR NEW.feedback IS DISTINCT FROM OLD.feedback OR NEW.ended_at IS "
            "DISTINCT FROM OLD.ended_at)) THEN RAISE EXCEPTION 'Immutable practice content'; "
            "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER practice_run_immutable BEFORE UPDATE ON practice_runs "
            "FOR EACH ROW EXECUTE FUNCTION ari_practice_run_immutable()"
        )
        op.execute(
            "CREATE TRIGGER practice_answers_immutable BEFORE UPDATE OR DELETE ON "
            "practice_answers FOR EACH ROW EXECUTE FUNCTION ari_clinical_immutable()"
        )
        op.execute(
            "CREATE FUNCTION ari_practice_answer_active() RETURNS trigger AS $$ BEGIN "
            "PERFORM id FROM practice_runs WHERE id=NEW.run_id AND status='active' FOR UPDATE; "
            "IF NOT FOUND THEN RAISE EXCEPTION 'Practice is not active'; END IF; "
            "RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER practice_answer_active BEFORE INSERT ON practice_answers "
            "FOR EACH ROW EXECUTE FUNCTION ari_practice_answer_active()"
        )


def downgrade():
    op.drop_table("practice_answers")
    op.drop_table("practice_runs")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION ari_practice_answer_active()")
        op.execute("DROP FUNCTION ari_practice_run_immutable()")
