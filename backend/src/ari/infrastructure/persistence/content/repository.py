"""SQLAlchemy implementation of the content repository: one transaction, every table."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from ari.content.domain.bundles import (
    BundleDraft,
    BundleDraftStatus,
    BundleVariantRequest,
    ScenarioRef,
)
from ari.content.domain.documents import (
    Actor,
    AiRun,
    Document,
    DocumentDeclaration,
    DocumentPage,
    DocumentSegment,
    DocumentStatus,
    ProtocolEvent,
    ProtocolVersion,
    SegmentStatus,
)
from ari.content.domain.pii import PiiFinding
from ari.content.domain.protocol import (
    GoldProtocol,
    ProtocolRecord,
    ProtocolReview,
    ProtocolStatus,
)
from ari.domain.geography import Land
from ari.infrastructure.persistence.content.rows import (
    AiRunRow,
    BundleDraftRow,
    DocumentPageRow,
    DocumentRow,
    DocumentSegmentRow,
    GoldProtocolRow,
    ProtocolEventRow,
    ProtocolReviewRow,
    ProtocolRow,
)


def _dt(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _record(payload: dict[str, Any]) -> ProtocolRecord:
    return ProtocolRecord.model_validate_json(json.dumps(payload, allow_nan=False))


class SqlContentRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def transaction(self) -> Iterator[SqlContentTransaction]:
        with Session(self.engine) as db, db.begin():
            yield SqlContentTransaction(db)


class SqlContentTransaction:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ----- documents -----------------------------------------------------------------

    def add_document(self, document: Document) -> None:
        declaration = document.declaration
        self.db.add(
            DocumentRow(
                id=document.id,
                filename=document.filename,
                size_bytes=document.size_bytes,
                page_count=document.page_count,
                storage_key=document.storage_key,
                uploaded_via=document.uploaded_via,
                uploaded_by_account_id=document.uploaded_by_account_id,
                declared_land=declaration.land.value if declaration.land else None,
                declared_city=declaration.city,
                declared_exam_body=declaration.exam_body,
                declared_exam_date=declaration.exam_date,
                declared_specialty=declaration.specialty,
                provenance=declaration.provenance,
                rights=declaration.rights,
                rights_evidence=declaration.rights_evidence,
                intended_use=declaration.intended_use,
                consent_declaration=declaration.consent_declaration,
                status=document.status.value,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
        )
        self.db.flush()

    def get_document(self, document_id: str) -> Document | None:
        row = self.db.get(DocumentRow, document_id)
        return self._document(row) if row else None

    def list_documents(
        self, *, status: DocumentStatus | None = None, land: Land | None = None
    ) -> tuple[Document, ...]:
        statement = select(DocumentRow).order_by(DocumentRow.created_at, DocumentRow.id)
        if status is not None:
            statement = statement.where(DocumentRow.status == status.value)
        if land is not None:
            statement = statement.where(DocumentRow.declared_land == land.value)
        return tuple(self._document(row) for row in self.db.scalars(statement))

    def set_document_status(
        self, document_id: str, status: DocumentStatus, *, page_count: int | None = None
    ) -> None:
        values: dict[str, Any] = {"status": status.value, "updated_at": datetime.now(UTC)}
        if page_count is not None:
            values["page_count"] = page_count
        self.db.execute(update(DocumentRow).where(DocumentRow.id == document_id).values(**values))

    @staticmethod
    def _document(row: DocumentRow) -> Document:
        return Document(
            id=row.id,
            filename=row.filename,
            size_bytes=row.size_bytes,
            storage_key=row.storage_key,
            uploaded_via=row.uploaded_via,  # type: ignore[arg-type]
            declaration=DocumentDeclaration(
                provenance=row.provenance,
                intended_use=row.intended_use,
                consent_declaration=row.consent_declaration,
                rights=row.rights,  # type: ignore[arg-type]
                rights_evidence=row.rights_evidence,
                land=Land(row.declared_land) if row.declared_land else None,
                city=row.declared_city,
                exam_body=row.declared_exam_body,
                exam_date=row.declared_exam_date,
                specialty=row.declared_specialty,
            ),
            status=DocumentStatus(row.status),
            page_count=row.page_count,
            uploaded_by_account_id=row.uploaded_by_account_id,
            created_at=_dt(row.created_at),
            updated_at=_dt(row.updated_at),
        )

    # ----- pages and segments ---------------------------------------------------------

    def add_pages(self, pages: Sequence[DocumentPage]) -> None:
        for page in pages:
            self.db.add(
                DocumentPageRow(
                    document_id=page.document_id,
                    page_number=page.page_number,
                    text=page.text,
                    char_count=page.char_count,
                    extractor=page.extractor,
                )
            )
        self.db.flush()

    def pages(self, document_id: str) -> tuple[DocumentPage, ...]:
        rows = self.db.scalars(
            select(DocumentPageRow)
            .where(DocumentPageRow.document_id == document_id)
            .order_by(DocumentPageRow.page_number)
        )
        return tuple(
            DocumentPage(
                document_id=row.document_id,
                page_number=row.page_number,
                text=row.text,
                extractor=row.extractor,
            )
            for row in rows
        )

    def add_segments(self, segments: Sequence[DocumentSegment]) -> None:
        for segment in segments:
            self.db.add(
                DocumentSegmentRow(
                    id=segment.id,
                    document_id=segment.document_id,
                    index=segment.index,
                    page_from=segment.page_from,
                    page_to=segment.page_to,
                    start_marker=segment.start_marker,
                    title_hint=segment.title_hint,
                    date_hint=segment.date_hint,
                    land_hint=segment.land_hint,
                    city_hint=segment.city_hint,
                    confidence=segment.confidence,
                    origin=segment.origin,
                    ai_run_id=segment.ai_run_id,
                    status=segment.status.value,
                    created_at=segment.created_at,
                )
            )
        self.db.flush()

    def segments(self, document_id: str) -> tuple[DocumentSegment, ...]:
        rows = self.db.scalars(
            select(DocumentSegmentRow)
            .where(DocumentSegmentRow.document_id == document_id)
            .order_by(DocumentSegmentRow.index)
        )
        return tuple(self._segment(row) for row in rows)

    def get_segment(self, segment_id: str) -> DocumentSegment | None:
        row = self.db.get(DocumentSegmentRow, segment_id)
        return self._segment(row) if row else None

    def set_segment_status(self, segment_id: str, status: SegmentStatus) -> None:
        self.db.execute(
            update(DocumentSegmentRow)
            .where(DocumentSegmentRow.id == segment_id)
            .values(status=status.value)
        )

    @staticmethod
    def _segment(row: DocumentSegmentRow) -> DocumentSegment:
        return DocumentSegment(
            id=row.id,
            document_id=row.document_id,
            index=row.index,
            page_from=row.page_from,
            page_to=row.page_to,
            start_marker=row.start_marker,
            confidence=row.confidence,
            origin=row.origin,  # type: ignore[arg-type]
            status=SegmentStatus(row.status),
            title_hint=row.title_hint,
            date_hint=row.date_hint,
            land_hint=row.land_hint,
            city_hint=row.city_hint,
            ai_run_id=row.ai_run_id,
            created_at=_dt(row.created_at),
        )

    # ----- protocols --------------------------------------------------------------------

    def add_protocol(self, protocol: ProtocolVersion) -> None:
        self.db.add(
            ProtocolRow(
                id=protocol.id,
                version=protocol.version,
                content_hash=protocol.content_hash,
                payload=protocol.record.model_dump(mode="json"),
                document_id=protocol.document_id,
                segment_id=protocol.segment_id,
                page_from=protocol.page_from,
                page_to=protocol.page_to,
                parent_version=protocol.parent_version,
                created_via=protocol.created_via,
                created_by_account_id=protocol.created_by_account_id,
                pii_findings=[f.model_dump(mode="json") for f in protocol.pii_findings],
                pii_detector_version=protocol.pii_detector_version,
                status=protocol.status.value,
                created_at=protocol.created_at,
            )
        )
        self.db.flush()

    def head(self, protocol_id: str, *, lock: bool = False) -> ProtocolVersion | None:
        statement = (
            select(ProtocolRow)
            .where(ProtocolRow.id == protocol_id)
            .order_by(ProtocolRow.version.desc())
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        row = self.db.scalar(statement)
        return self._protocol(row) if row else None

    def get_protocol(self, protocol_id: str, version: int) -> ProtocolVersion | None:
        row = self.db.get(ProtocolRow, (protocol_id, version))
        return self._protocol(row) if row else None

    def versions(self, protocol_id: str) -> tuple[ProtocolVersion, ...]:
        rows = self.db.scalars(
            select(ProtocolRow).where(ProtocolRow.id == protocol_id).order_by(ProtocolRow.version)
        )
        return tuple(self._protocol(row) for row in rows)

    def list_heads(
        self,
        *,
        status: ProtocolStatus | None = None,
        document_id: str | None = None,
        land: Land | None = None,
    ) -> tuple[ProtocolVersion, ...]:
        latest = (
            select(ProtocolRow.id, func.max(ProtocolRow.version).label("version"))
            .group_by(ProtocolRow.id)
            .subquery()
        )
        statement = (
            select(ProtocolRow)
            .join(
                latest,
                (ProtocolRow.id == latest.c.id) & (ProtocolRow.version == latest.c.version),
            )
            .order_by(ProtocolRow.document_id, ProtocolRow.id)
        )
        if status is not None:
            statement = statement.where(ProtocolRow.status == status.value)
        if document_id is not None:
            statement = statement.where(ProtocolRow.document_id == document_id)
        if land is not None:
            statement = statement.where(
                ProtocolRow.payload["location"]["land"].astext == land.value
            )
        return tuple(self._protocol(row) for row in self.db.scalars(statement))

    def set_protocol_status(self, protocol_id: str, version: int, status: ProtocolStatus) -> None:
        self.db.execute(
            update(ProtocolRow)
            .where(ProtocolRow.id == protocol_id, ProtocolRow.version == version)
            .values(status=status.value)
        )

    @staticmethod
    def _protocol(row: ProtocolRow) -> ProtocolVersion:
        record = _record(row.payload)
        if record.content_hash != row.content_hash:
            raise ValueError(f"Intégrité du protocole {row.id}@{row.version} invalide")
        return ProtocolVersion(
            id=row.id,
            version=row.version,
            record=record,
            document_id=row.document_id,
            segment_id=row.segment_id,
            created_via=row.created_via,  # type: ignore[arg-type]
            status=ProtocolStatus(row.status),
            pii_findings=tuple(
                PiiFinding.model_validate_json(json.dumps(item)) for item in row.pii_findings
            ),
            pii_detector_version=row.pii_detector_version,
            parent_version=row.parent_version,
            created_by_account_id=row.created_by_account_id,
            created_at=_dt(row.created_at),
        )

    # ----- reviews, events, gold, AI runs --------------------------------------------------

    def add_review(self, review: ProtocolReview) -> None:
        self.db.add(
            ProtocolReviewRow(
                id=review.id,
                protocol_id=review.protocol_id,
                protocol_version=review.protocol_version,
                protocol_hash=review.protocol_hash,
                stage=review.stage,
                decision=review.decision,
                reviewer_account_id=review.reviewer_account_id,
                reviewer_name=review.reviewer_name,
                pii_override_note=review.pii_override_note,
                payload=review.model_dump(mode="json"),
                created_at=review.reviewed_at,
            )
        )
        self.db.flush()

    def reviews(self, protocol_id: str) -> tuple[ProtocolReview, ...]:
        rows = self.db.scalars(
            select(ProtocolReviewRow)
            .where(ProtocolReviewRow.protocol_id == protocol_id)
            .order_by(ProtocolReviewRow.sequence)
        )
        return tuple(ProtocolReview.model_validate_json(json.dumps(row.payload)) for row in rows)

    def add_event(self, event: ProtocolEvent) -> None:
        self.db.add(
            ProtocolEventRow(
                id=event.id,
                protocol_id=event.protocol_id,
                protocol_version=event.protocol_version,
                event_type=event.event_type,
                actor_kind=event.actor.kind,
                actor_account_id=event.actor.account_id,
                payload={"actor_name": event.actor.name, **event.payload},
                created_at=event.created_at,
            )
        )
        self.db.flush()

    def events(self, protocol_id: str) -> tuple[ProtocolEvent, ...]:
        rows = self.db.scalars(
            select(ProtocolEventRow)
            .where(ProtocolEventRow.protocol_id == protocol_id)
            .order_by(ProtocolEventRow.created_at, ProtocolEventRow.id)
        )
        events = []
        for row in rows:
            payload = dict(row.payload)
            name = str(payload.pop("actor_name", ""))
            events.append(
                ProtocolEvent(
                    id=row.id,
                    protocol_id=row.protocol_id,
                    protocol_version=row.protocol_version,
                    event_type=row.event_type,
                    actor=Actor(kind=row.actor_kind, name=name, account_id=row.actor_account_id),  # type: ignore[arg-type]
                    payload=payload,
                    created_at=_dt(row.created_at),
                )
            )
        return tuple(events)

    def add_gold(self, gold: GoldProtocol) -> None:
        self.db.add(
            GoldProtocolRow(
                protocol_id=gold.protocol_id,
                protocol_version=gold.protocol_version,
                protocol_hash=gold.protocol_hash,
                gold_hash=gold.content_hash,
                land=gold.location.land.value if gold.location.land else "",
                city=gold.location.city,
                exam_body=gold.location.exam_body,
                exam_date=gold.location.exam_date,
                specialty=gold.location.specialty,
                document_id=gold.document_id,
                payload=gold.model_dump(mode="json"),
                frozen_by_account_id=gold.frozen_by_account_id,
                frozen_at=gold.frozen_at,
            )
        )
        self.db.flush()

    def get_gold(self, protocol_id: str) -> GoldProtocol | None:
        row = self.db.get(GoldProtocolRow, protocol_id)
        return GoldProtocol.model_validate_json(json.dumps(row.payload)) if row else None

    def list_gold(self, *, land: Land | None = None) -> tuple[GoldProtocol, ...]:
        statement = select(GoldProtocolRow).order_by(
            GoldProtocolRow.frozen_at, GoldProtocolRow.protocol_id
        )
        if land is not None:
            statement = statement.where(GoldProtocolRow.land == land.value)
        return tuple(
            GoldProtocol.model_validate_json(json.dumps(row.payload))
            for row in self.db.scalars(statement)
        )

    # ----- bundle drafts -----------------------------------------------------------

    def add_bundle_draft(self, draft: BundleDraft) -> None:
        self.db.add(
            BundleDraftRow(
                id=draft.id,
                gold_protocol_id=draft.gold_protocol_id,
                gold_hash=draft.gold_hash,
                request=draft.request.model_dump(mode="json"),
                status=draft.status.value,
                bundle=draft.bundle,
                bundle_hash=draft.bundle_hash,
                case_id=draft.case_id,
                case_version=draft.case_version,
                scenario_refs=[
                    {"id": ref.id, "version": ref.version, "phase": ref.phase}
                    for ref in draft.scenario_refs
                ],
                validation_errors=list(draft.validation_errors),
                ai_run_id=draft.ai_run_id,
                created_by_account_id=draft.created_by_account_id,
                created_at=draft.created_at,
                imported_at=draft.imported_at,
            )
        )
        self.db.flush()

    def get_bundle_draft(self, draft_id: str, *, lock: bool = False) -> BundleDraft | None:
        statement = select(BundleDraftRow).where(BundleDraftRow.id == draft_id)
        if lock:
            statement = statement.with_for_update()
        row = self.db.scalar(statement)
        return self._bundle_draft(row) if row else None

    def list_bundle_drafts(self, *, gold_protocol_id: str | None = None) -> tuple[BundleDraft, ...]:
        statement = select(BundleDraftRow).order_by(
            BundleDraftRow.created_at.desc(), BundleDraftRow.id
        )
        if gold_protocol_id is not None:
            statement = statement.where(BundleDraftRow.gold_protocol_id == gold_protocol_id)
        return tuple(self._bundle_draft(row) for row in self.db.scalars(statement))

    def mark_bundle_draft_imported(self, draft_id: str) -> None:
        self.db.execute(
            update(BundleDraftRow)
            .where(BundleDraftRow.id == draft_id)
            .values(status=BundleDraftStatus.IMPORTED.value, imported_at=datetime.now(UTC))
        )

    @staticmethod
    def _bundle_draft(row: BundleDraftRow) -> BundleDraft:
        return BundleDraft(
            id=row.id,
            gold_protocol_id=row.gold_protocol_id,
            gold_hash=row.gold_hash,
            request=BundleVariantRequest.model_validate_json(json.dumps(row.request)),
            status=BundleDraftStatus(row.status),
            bundle=row.bundle,
            bundle_hash=row.bundle_hash,
            case_id=row.case_id,
            case_version=row.case_version,
            scenario_refs=tuple(
                ScenarioRef(id=ref["id"], version=ref["version"], phase=ref["phase"])
                for ref in row.scenario_refs
            ),
            validation_errors=tuple(row.validation_errors),
            ai_run_id=row.ai_run_id,
            created_by_account_id=row.created_by_account_id,
            created_at=_dt(row.created_at),
            imported_at=_dt(row.imported_at) if row.imported_at else None,
        )

    def add_ai_run(self, run: AiRun) -> None:
        self.db.add(
            AiRunRow(
                id=run.id,
                job_id=run.job_id,
                operation=run.operation,
                provider=run.provider,
                model=run.model,
                prompt_version=run.prompt_version,
                prompt_hash=run.prompt_hash,
                schema_name=run.schema_name,
                input_hash=run.input_hash,
                document_id=run.document_id,
                segment_id=run.segment_id,
                protocol_id=run.protocol_id,
                status=run.status,
                latency_ms=run.latency_ms,
                usage=run.usage,
                provider_request_id=run.provider_request_id,
                error_code=run.error_code,
                error_message=run.error_message,
                retryable=run.retryable,
                created_at=run.created_at,
            )
        )
        self.db.flush()

    def ai_runs(self, *, document_id: str | None = None) -> tuple[AiRun, ...]:
        statement = select(AiRunRow).order_by(AiRunRow.created_at, AiRunRow.id)
        if document_id is not None:
            statement = statement.where(AiRunRow.document_id == document_id)
        return tuple(
            AiRun(
                id=row.id,
                operation=row.operation,
                provider=row.provider,
                model=row.model,
                schema_name=row.schema_name,
                input_hash=row.input_hash,
                status=row.status,
                latency_ms=row.latency_ms,
                usage=dict(row.usage),
                job_id=row.job_id,
                prompt_version=row.prompt_version,
                prompt_hash=row.prompt_hash,
                document_id=row.document_id,
                segment_id=row.segment_id,
                protocol_id=row.protocol_id,
                provider_request_id=row.provider_request_id,
                error_code=row.error_code,
                error_message=row.error_message,
                retryable=row.retryable,
                created_at=_dt(row.created_at),
            )
            for row in self.db.scalars(statement)
        )
