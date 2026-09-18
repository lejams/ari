"""Documents, pages, segments, jobs and protocol versions as the services see them.

Plain frozen dataclasses: the persistence adapter maps them to rows, nothing else knows SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from ari.content.domain.pii import PiiFinding
from ari.content.domain.protocol import ProtocolRecord, ProtocolStatus
from ari.domain.geography import Land
from ari.domain.models import utc_now

Rights = Literal["unknown", "incompatible", "compatible"]
UploadedVia = Literal["cli", "backoffice"]
ActorKind = Literal["system", "account", "cli"]


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    TEXT_EXTRACTED = "text_extracted"
    SEGMENTED = "segmented"
    EXTRACTED = "extracted"
    FAILED = "failed"


class SegmentStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    FAILED = "failed"
    TOO_LONG = "too_long"
    DISCARDED = "discarded"


class JobType(StrEnum):
    EXTRACT_TEXT = "extract_text"
    SEGMENT_DOCUMENT = "segment_document"
    EXTRACT_PROTOCOL = "extract_protocol"
    GENERATE_BUNDLE_DRAFT = "generate_bundle_draft"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who performs an action: the CLI operator, a back-office account or the worker."""

    kind: ActorKind
    name: str
    account_id: str | None = None


SYSTEM_ACTOR = Actor(kind="system", name="ari.worker")


@dataclass(frozen=True, slots=True)
class DocumentDeclaration:
    """What the uploader states about a PDF: where it comes from and what may be done with it."""

    provenance: str
    intended_use: str
    consent_declaration: str
    rights: Rights = "unknown"
    rights_evidence: str | None = None
    land: Land | None = None
    city: str | None = None
    exam_body: str | None = None
    exam_date: str | None = None  # YYYY-MM
    specialty: str | None = None


@dataclass(frozen=True, slots=True)
class Document:
    id: str  # SHA-256 of the bytes
    filename: str
    size_bytes: int
    storage_key: str
    uploaded_via: UploadedVia
    declaration: DocumentDeclaration
    status: DocumentStatus = DocumentStatus.UPLOADED
    page_count: int | None = None
    uploaded_by_account_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class DocumentPage:
    document_id: str
    page_number: int
    text: str
    extractor: str

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True, slots=True)
class DocumentSegment:
    id: str
    document_id: str
    index: int
    page_from: int
    page_to: int
    start_marker: str
    confidence: float
    origin: Literal["ai", "manual"]
    status: SegmentStatus = SegmentStatus.PENDING
    title_hint: str | None = None
    date_hint: str | None = None
    land_hint: str | None = None
    city_hint: str | None = None
    ai_run_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    type: JobType
    payload: dict[str, Any]
    status: JobStatus = JobStatus.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    available_at: datetime = field(default_factory=utc_now)
    locked_at: datetime | None = None
    locked_by: str | None = None
    last_error: str | None = None
    document_id: str | None = None
    protocol_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProtocolVersion:
    """One stored version of a protocol record with its bookkeeping."""

    id: str
    version: int
    record: ProtocolRecord
    document_id: str
    segment_id: str
    created_via: Literal["ai", "doctor", "owner"]
    status: ProtocolStatus
    pii_findings: tuple[PiiFinding, ...]
    pii_detector_version: str
    parent_version: int | None = None
    created_by_account_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    @property
    def content_hash(self) -> str:
        return self.record.content_hash

    @property
    def page_from(self) -> int:
        return self.record.source.page_from

    @property
    def page_to(self) -> int:
        return self.record.source.page_to


@dataclass(frozen=True, slots=True)
class ProtocolEvent:
    id: str
    protocol_id: str
    protocol_version: int
    event_type: str
    actor: Actor
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class AiRun:
    """The pipeline's trace of one model call (the equivalent of a learner `ExecutionRecord`)."""

    id: str
    operation: str
    provider: str
    model: str
    schema_name: str
    input_hash: str
    status: str
    latency_ms: int
    usage: dict[str, Any]
    job_id: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    document_id: str | None = None
    segment_id: str | None = None
    protocol_id: str | None = None
    provider_request_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = True
    created_at: datetime = field(default_factory=utc_now)
