"""The content revision creates the schema and the database itself keeps content immutable."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.infrastructure.persistence.content import (
    DocumentPageRow,
    DocumentRow,
    create_content_engine,
)
from ari.infrastructure.persistence.schema import alembic_config

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _document(document_id: str = "a" * 64, land: str = "Bayern") -> DocumentRow:
    return DocumentRow(
        id=document_id,
        filename="protokolle.pdf",
        size_bytes=1234,
        page_count=None,
        storage_key=f"documents/aa/{document_id}.pdf",
        uploaded_via="cli",
        declared_land=land,
        provenance="Test fixture",
        rights="compatible",
        rights_evidence="Synthetic",
        intended_use="tests",
        consent_declaration="Synthetic test content",
        status="uploaded",
        created_at=NOW,
        updated_at=NOW,
    )


def test_content_migration_matches_the_models(content_database_url: str) -> None:
    command.check(alembic_config(content_database_url, "content"))
    engine = create_content_engine(content_database_url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "documents",
        "document_pages",
        "document_segments",
        "jobs",
        "protocols",
        "protocol_reviews",
        "protocol_events",
        "gold_protocols",
        "ai_runs",
    } <= tables
    with engine.connect() as connection:
        functions = set(
            connection.execute(
                text("SELECT proname FROM pg_proc WHERE proname LIKE 'ari_%'")
            ).scalars()
        )
    assert functions == {"ari_immutable", "ari_only_columns_mutable"}
    engine.dispose()


def test_documents_only_move_their_status_and_pages_are_append_only(
    content_database_url: str,
) -> None:
    engine = create_content_engine(content_database_url)
    with Session(engine) as db, db.begin():
        db.add(_document())
        db.flush()  # No ORM relationships: dependent rows are flushed explicitly.
        db.add(
            DocumentPageRow(
                document_id="a" * 64, page_number=1, text="Seite 1", char_count=7, extractor="t"
            )
        )
    # Status, page count and updated_at may change; the guard sees no content change.
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE documents SET status='text_extracted', page_count=1, updated_at=now() "
                "WHERE id=:id"
            ),
            {"id": "a" * 64},
        )
    for sql in (
        "UPDATE documents SET filename='other.pdf'",
        "UPDATE documents SET declared_land='Berlin'",
        "DELETE FROM documents",
        "UPDATE document_pages SET text='altered'",
        "DELETE FROM document_pages",
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(sql))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM documents")).scalar() == (
            "text_extracted"
        )
    # The declared Land is the same closed list as everywhere else.
    with pytest.raises(IntegrityError), Session(engine) as db, db.begin():
        db.add(_document("b" * 64, land="Atlantis"))
    engine.dispose()
