"""Record explicit Training/Exam choice without inventing historical mode."""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0008"
down_revision = "20260904_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voice_learning_context",
        sa.Column("session_id", sa.String, primary_key=True),
        sa.Column("mode", sa.String, nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.CheckConstraint("mode IN ('training','exam')", name="ck_voice_learning_mode"),
    )
    op.create_table(
        "voice_start_requests",
        sa.Column("learner_id", sa.String, primary_key=True),
        sa.Column("request_id", sa.String, primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String, nullable=False, unique=True),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
    )
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER voice_learning_no_{action.lower()} BEFORE {action} "
                "ON voice_learning_context BEGIN "
                "SELECT RAISE(ABORT, 'Immutable learning mode'); END"
            )
    elif op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER voice_learning_immutable BEFORE UPDATE OR DELETE ON "
            "voice_learning_context FOR EACH ROW EXECUTE FUNCTION ari_clinical_immutable()"
        )
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER voice_start_no_{action.lower()} BEFORE {action} "
                "ON voice_start_requests BEGIN SELECT RAISE(ABORT, 'Immutable start request'); END"
            )
    elif op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER voice_start_immutable BEFORE UPDATE OR DELETE ON "
            "voice_start_requests FOR EACH ROW EXECUTE FUNCTION ari_clinical_immutable()"
        )


def downgrade():
    op.drop_table("voice_start_requests")
    op.drop_table("voice_learning_context")
