"""Tables of the content database. Content rows are append-only; only statuses move.

Immutability is enforced by the triggers of the content migration (see the revision and
`infrastructure/persistence/ddl.py`), not by application discipline.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ari.domain.geography import Land
from ari.infrastructure.persistence.content.base import ContentBase

CONTENT_JSON = JSONB()

DOCUMENT_STATUSES = ("uploaded", "text_extracted", "segmented", "extracted", "failed")
SEGMENT_STATUSES = ("pending", "extracted", "failed", "too_long", "discarded")
JOB_TYPES = ("extract_text", "segment_document", "extract_protocol", "generate_bundle_draft")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "dead")
PROTOCOL_STATUSES = (
    "extracted",
    "doctor_review",
    "changes_requested",
    "doctor_approved",
    "gold",
    "rejected",
    "superseded",
)
RIGHTS = ("unknown", "incompatible", "compatible")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


LAND_VALUES = _in(tuple(land.value for land in Land))


class DocumentRow(ContentBase):
    """One uploaded PDF, addressed by its SHA-256. The bytes live in the document storage."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(DOCUMENT_STATUSES)})", name="ck_document_status"),
        CheckConstraint(f"rights IN ({_in(RIGHTS)})", name="ck_document_rights"),
        CheckConstraint(
            f"declared_land IS NULL OR declared_land IN ({LAND_VALUES})", name="ck_document_land"
        ),
        CheckConstraint("uploaded_via IN ('cli', 'backoffice')", name="ck_document_uploaded_via"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_key: Mapped[str] = mapped_column(String)
    uploaded_via: Mapped[str] = mapped_column(String)
    uploaded_by_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    declared_land: Mapped[str | None] = mapped_column(String, nullable=True)
    declared_city: Mapped[str | None] = mapped_column(String, nullable=True)
    declared_exam_body: Mapped[str | None] = mapped_column(String, nullable=True)
    declared_exam_date: Mapped[str | None] = mapped_column(String(7), nullable=True)
    declared_specialty: Mapped[str | None] = mapped_column(String, nullable=True)
    provenance: Mapped[str] = mapped_column(Text)
    rights: Mapped[str] = mapped_column(String)
    rights_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    intended_use: Mapped[str] = mapped_column(Text)
    consent_declaration: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentPageRow(ContentBase):
    __tablename__ = "document_pages"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    extractor: Mapped[str] = mapped_column(String)


class DocumentSegmentRow(ContentBase):
    """A span of pages the segmentation step (or a human) identified as one protocol."""

    __tablename__ = "document_segments"
    __table_args__ = (
        UniqueConstraint("document_id", "index", name="uq_segment_document_index"),
        CheckConstraint(f"status IN ({_in(SEGMENT_STATUSES)})", name="ck_segment_status"),
        CheckConstraint("origin IN ('ai', 'manual')", name="ck_segment_origin"),
        CheckConstraint("page_from >= 1 AND page_to >= page_from", name="ck_segment_pages"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    page_from: Mapped[int] = mapped_column(Integer)
    page_to: Mapped[int] = mapped_column(Integer)
    start_marker: Mapped[str] = mapped_column(Text)
    title_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    date_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    land_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    city_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String)
    ai_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobRow(ContentBase):
    """The background queue. Claimed with FOR UPDATE SKIP LOCKED; the only fully mutable table."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_available_at", "status", "available_at"),
        CheckConstraint(f"type IN ({_in(JOB_TYPES)})", name="ck_job_type"),
        CheckConstraint(f"status IN ({_in(JOB_STATUSES)})", name="ck_job_status"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(CONTENT_JSON)
    status: Mapped[str] = mapped_column(String)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True, index=True
    )
    protocol_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProtocolRow(ContentBase):
    """One version of a protocol record. A correction is a new version; only `status` moves."""

    __tablename__ = "protocols"
    __table_args__ = (
        UniqueConstraint("id", "version", "content_hash", name="uq_protocol_identity_hash"),
        CheckConstraint(f"status IN ({_in(PROTOCOL_STATUSES)})", name="ck_protocol_status"),
        CheckConstraint("created_via IN ('ai', 'doctor', 'owner')", name="ck_protocol_created_via"),
        CheckConstraint("version >= 1", name="ck_protocol_version"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(CONTENT_JSON)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    segment_id: Mapped[str] = mapped_column(ForeignKey("document_segments.id"))
    page_from: Mapped[int] = mapped_column(Integer)
    page_to: Mapped[int] = mapped_column(Integer)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_via: Mapped[str] = mapped_column(String)
    created_by_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pii_findings: Mapped[list[dict[str, Any]]] = mapped_column(CONTENT_JSON)
    pii_detector_version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProtocolReviewRow(ContentBase):
    """A doctor or owner decision on the exact hash of one protocol version. Append-only."""

    __tablename__ = "protocol_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["protocol_id", "protocol_version", "protocol_hash"],
            ["protocols.id", "protocols.version", "protocols.content_hash"],
        ),
        CheckConstraint("stage IN ('doctor', 'owner')", name="ck_protocol_review_stage"),
        CheckConstraint(
            "decision IN ('approve', 'request_changes', 'reject')",
            name="ck_protocol_review_decision",
        ),
    )
    sequence: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    id: Mapped[str] = mapped_column(String, unique=True)
    protocol_id: Mapped[str] = mapped_column(String)
    protocol_version: Mapped[int] = mapped_column(Integer)
    protocol_hash: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    reviewer_account_id: Mapped[str] = mapped_column(String)
    reviewer_name: Mapped[str] = mapped_column(String)
    pii_override_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(CONTENT_JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProtocolEventRow(ContentBase):
    """Audit trail of every transition; who, what, when. Append-only."""

    __tablename__ = "protocol_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["protocol_id", "protocol_version"], ["protocols.id", "protocols.version"]
        ),
        CheckConstraint("actor_kind IN ('system', 'account', 'cli')", name="ck_event_actor_kind"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String, index=True)
    protocol_version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String)
    actor_kind: Mapped[str] = mapped_column(String)
    actor_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(CONTENT_JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GoldProtocolRow(ContentBase):
    """The frozen, owner-validated protocol: the ground truth every training case derives from."""

    __tablename__ = "gold_protocols"
    __table_args__ = (
        ForeignKeyConstraint(
            ["protocol_id", "protocol_version", "protocol_hash"],
            ["protocols.id", "protocols.version", "protocols.content_hash"],
        ),
        CheckConstraint(f"land IN ({LAND_VALUES})", name="ck_gold_land"),
    )
    protocol_id: Mapped[str] = mapped_column(String, primary_key=True)
    protocol_version: Mapped[int] = mapped_column(Integer)
    protocol_hash: Mapped[str] = mapped_column(String(64))
    gold_hash: Mapped[str] = mapped_column(String(64), unique=True)
    land: Mapped[str] = mapped_column(String)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    exam_body: Mapped[str | None] = mapped_column(String, nullable=True)
    exam_date: Mapped[str | None] = mapped_column(String(7), nullable=True)
    specialty: Mapped[str | None] = mapped_column(String, nullable=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(CONTENT_JSON)
    frozen_by_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AiRunRow(ContentBase):
    """Every model call of the pipeline: provider, model, prompt version, input hash, usage."""

    __tablename__ = "ai_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_name: Mapped[str] = mapped_column(String)
    input_hash: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True, index=True
    )
    segment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    protocol_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    latency_ms: Mapped[int] = mapped_column(Integer)
    usage: Mapped[dict[str, Any]] = mapped_column(CONTENT_JSON)
    provider_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
