"""Versioned clinical registry; legacy case/session content remains untouched."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260904_0004"
down_revision = "20260903_0003"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")


def _text(name, *, primary_key=False, length=None):
    return sa.Column(name, sa.String(length), primary_key=primary_key, nullable=False)


def _payload():
    return sa.Column("payload", JSON_TYPE, nullable=False)


def upgrade():
    op.create_table(
        "clinical_sources",
        _text("id", primary_key=True),
        _text("content_hash", length=64),
        _payload(),
    )
    for table in ("clinical_rubrics", "clinical_terminology", "clinical_cases"):
        op.create_table(
            table,
            _text("id", primary_key=True),
            _text("version", primary_key=True),
            _text("content_hash", length=64),
            _payload(),
            sa.UniqueConstraint("id", "version", "content_hash"),
        )
    op.create_table(
        "clinical_case_sources",
        _text("case_id", primary_key=True),
        _text("case_version", primary_key=True),
        _text("source_id", primary_key=True),
        sa.ForeignKeyConstraint(
            ["case_id", "case_version"], ["clinical_cases.id", "clinical_cases.version"]
        ),
        sa.ForeignKeyConstraint(["source_id"], ["clinical_sources.id"]),
    )
    op.create_table(
        "clinical_scenarios",
        _text("id", primary_key=True),
        _text("version", primary_key=True),
        _text("content_hash", length=64),
        _payload(),
        _text("case_id"),
        _text("case_version"),
        _text("case_hash", length=64),
        _text("rubric_id"),
        _text("rubric_version"),
        _text("rubric_hash", length=64),
        _text("terminology_id"),
        _text("terminology_version"),
        _text("terminology_hash", length=64),
        _text("phase"),
        _text("status"),
        sa.UniqueConstraint("id", "version", "content_hash"),
        sa.ForeignKeyConstraint(
            ["case_id", "case_version", "case_hash"],
            ["clinical_cases.id", "clinical_cases.version", "clinical_cases.content_hash"],
        ),
        sa.ForeignKeyConstraint(
            ["rubric_id", "rubric_version", "rubric_hash"],
            ["clinical_rubrics.id", "clinical_rubrics.version", "clinical_rubrics.content_hash"],
        ),
        sa.ForeignKeyConstraint(
            ["terminology_id", "terminology_version", "terminology_hash"],
            [
                "clinical_terminology.id",
                "clinical_terminology.version",
                "clinical_terminology.content_hash",
            ],
        ),
        sa.CheckConstraint(
            "status IN ('draft_unvalidated', 'published', 'withdrawn')",
            name="ck_clinical_workflow_status",
        ),
        sa.CheckConstraint(
            "phase IN ('arzt_patient', 'arzt_arzt', 'fachbegriffe', 'arztbrief')",
            name="ck_clinical_phase",
        ),
    )
    op.create_index(
        "uq_clinical_published_case_phase",
        "clinical_scenarios",
        ["case_id", "case_version", "phase"],
        unique=True,
        sqlite_where=sa.text("status = 'published'"),
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "clinical_reviews",
        sa.Column("sequence", sa.Integer, primary_key=True, autoincrement=True),
        _text("id"),
        _text("scenario_id"),
        _text("scenario_version"),
        _text("scenario_hash", length=64),
        _payload(),
        sa.UniqueConstraint("id"),
        sa.ForeignKeyConstraint(
            ["scenario_id", "scenario_version", "scenario_hash"],
            [
                "clinical_scenarios.id",
                "clinical_scenarios.version",
                "clinical_scenarios.content_hash",
            ],
        ),
    )
    op.create_table(
        "clinical_publication_events",
        _text("id", primary_key=True),
        _text("scenario_id"),
        _text("scenario_version"),
        _payload(),
        sa.ForeignKeyConstraint(
            ["scenario_id", "scenario_version"],
            ["clinical_scenarios.id", "clinical_scenarios.version"],
        ),
    )
    op.create_table(
        "clinical_session_pins",
        _text("session_id", primary_key=True),
        _text("scenario_id"),
        _text("scenario_version"),
        _text("scenario_hash", length=64),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(
            ["scenario_id", "scenario_version", "scenario_hash"],
            [
                "clinical_scenarios.id",
                "clinical_scenarios.version",
                "clinical_scenarios.content_hash",
            ],
        ),
    )
    immutable_tables = (
        "clinical_sources",
        "clinical_rubrics",
        "clinical_terminology",
        "clinical_cases",
        "clinical_case_sources",
        "clinical_reviews",
        "clinical_publication_events",
        "clinical_session_pins",
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in immutable_tables:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Clinical content is immutable'); END"
                )
        columns = (
            "id",
            "version",
            "content_hash",
            "payload",
            "case_id",
            "case_version",
            "case_hash",
            "rubric_id",
            "rubric_version",
            "rubric_hash",
            "terminology_id",
            "terminology_version",
            "terminology_hash",
            "phase",
        )
        condition = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in columns)
        op.execute(
            "CREATE TRIGGER clinical_scenario_content BEFORE UPDATE ON clinical_scenarios "
            f"WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Immutable scenario'); END"
        )
        op.execute(
            "CREATE TRIGGER clinical_scenario_delete BEFORE DELETE ON clinical_scenarios "
            "BEGIN SELECT RAISE(ABORT, 'Immutable scenario'); END"
        )
    elif dialect == "postgresql":
        op.execute("""CREATE FUNCTION ari_clinical_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Clinical content is immutable'; END;
            $$ LANGUAGE plpgsql""")
        for table in immutable_tables:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION ari_clinical_immutable()"
            )
        op.execute("""CREATE FUNCTION ari_scenario_immutable() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Immutable scenario'; END IF;
                IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
                    RAISE EXCEPTION 'Immutable scenario';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER clinical_scenario_immutable BEFORE UPDATE OR DELETE "
            "ON clinical_scenarios FOR EACH ROW EXECUTE FUNCTION ari_scenario_immutable()"
        )


def downgrade():
    raise RuntimeError(
        "Clinical history is append-only; use a verified backup to reverse migration"
    )
