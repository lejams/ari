"""Private non-executable draft intake; no changes to existing content or hashes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260904_0005"
down_revision = "20260904_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "clinical_draft_batches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    )
    op.create_table(
        "clinical_draft_cases",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("version", sa.String, primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["clinical_draft_batches.id"]),
        sa.CheckConstraint("status = 'draft_unvalidated'", name="ck_clinical_intake_draft_only"),
    )
    for table in ("clinical_draft_cases", "clinical_draft_batches"):
        if op.get_bind().dialect.name == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Draft intake is immutable'); END"
                )
        elif op.get_bind().dialect.name == "postgresql":
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION ari_clinical_immutable()"
            )


def downgrade():
    op.drop_table("clinical_draft_cases")
    op.drop_table("clinical_draft_batches")
