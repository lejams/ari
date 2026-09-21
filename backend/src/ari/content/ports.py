"""Interfaces the content services depend on; infrastructure implements them."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from ari.content.domain.bundles import BundleDraft
from ari.content.domain.documents import (
    AiRun,
    Document,
    DocumentPage,
    DocumentSegment,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    ProtocolEvent,
    ProtocolVersion,
    SegmentStatus,
)
from ari.content.domain.protocol import GoldProtocol, ProtocolReview, ProtocolStatus
from ari.domain.geography import Land


class DocumentStorage(Protocol):
    """Where PDF bytes live: content-addressed, never in the database."""

    def put(self, data: bytes, *, key: str) -> Path: ...

    def path(self, key: str) -> Path: ...

    def exists(self, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class PdfText:
    page_count: int
    pages: tuple[str, ...]
    extractor: str  # library and version, stored with every page


class PdfTextExtractor(Protocol):
    def extract(self, path: Path) -> PdfText: ...

    def render_page(self, path: Path, page_number: int, *, dpi: int = 110) -> bytes: ...


class JobQueue(Protocol):
    def enqueue(
        self,
        type: JobType,
        payload: dict[str, object],
        *,
        document_id: str | None = None,
        protocol_id: str | None = None,
    ) -> Job: ...

    def claim(self, worker_id: str, types: Sequence[JobType] | None = None) -> Job | None: ...

    def succeed(self, job_id: str) -> None: ...

    def fail(self, job_id: str, error: str, *, retry_in: timedelta | None) -> Job: ...

    def retry(self, job_id: str) -> Job: ...

    def get(self, job_id: str) -> Job: ...

    def list(self, status: JobStatus | None = None) -> tuple[Job, ...]: ...

    def reclaim_stale(self, older_than: timedelta) -> int: ...


class ContentTransaction(Protocol):
    """Every read and write of the content database inside one transaction."""

    def add_document(self, document: Document) -> None: ...

    def get_document(self, document_id: str) -> Document | None: ...

    def list_documents(
        self, *, status: DocumentStatus | None = None, land: Land | None = None
    ) -> tuple[Document, ...]: ...

    def set_document_status(
        self, document_id: str, status: DocumentStatus, *, page_count: int | None = None
    ) -> None: ...

    def add_pages(self, pages: Sequence[DocumentPage]) -> None: ...

    def pages(self, document_id: str) -> tuple[DocumentPage, ...]: ...

    def add_segments(self, segments: Sequence[DocumentSegment]) -> None: ...

    def segments(self, document_id: str) -> tuple[DocumentSegment, ...]: ...

    def get_segment(self, segment_id: str) -> DocumentSegment | None: ...

    def set_segment_status(self, segment_id: str, status: SegmentStatus) -> None: ...

    def add_protocol(self, protocol: ProtocolVersion) -> None: ...

    def head(self, protocol_id: str, *, lock: bool = False) -> ProtocolVersion | None: ...

    def get_protocol(self, protocol_id: str, version: int) -> ProtocolVersion | None: ...

    def versions(self, protocol_id: str) -> tuple[ProtocolVersion, ...]: ...

    def list_heads(
        self,
        *,
        status: ProtocolStatus | None = None,
        document_id: str | None = None,
        land: Land | None = None,
    ) -> tuple[ProtocolVersion, ...]: ...

    def set_protocol_status(
        self, protocol_id: str, version: int, status: ProtocolStatus
    ) -> None: ...

    def add_review(self, review: ProtocolReview) -> None: ...

    def reviews(self, protocol_id: str) -> tuple[ProtocolReview, ...]: ...

    def add_event(self, event: ProtocolEvent) -> None: ...

    def events(self, protocol_id: str) -> tuple[ProtocolEvent, ...]: ...

    def add_gold(self, gold: GoldProtocol) -> None: ...

    def get_gold(self, protocol_id: str) -> GoldProtocol | None: ...

    def list_gold(self, *, land: Land | None = None) -> tuple[GoldProtocol, ...]: ...

    def add_bundle_draft(self, draft: BundleDraft) -> None: ...

    def get_bundle_draft(self, draft_id: str, *, lock: bool = False) -> BundleDraft | None: ...

    def list_bundle_drafts(
        self, *, gold_protocol_id: str | None = None
    ) -> tuple[BundleDraft, ...]: ...

    def mark_bundle_draft_imported(self, draft_id: str) -> None: ...

    def add_ai_run(self, run: AiRun) -> None: ...

    def ai_runs(self, *, document_id: str | None = None) -> tuple[AiRun, ...]: ...


class ContentRepository(Protocol):
    def transaction(self) -> AbstractContextManager[ContentTransaction]: ...
