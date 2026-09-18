"""Documents: upload with declaration, pages and images, segments, jobs, release to review."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse

from ari.backoffice_api.container import Backoffice
from ari.backoffice_api.deps import actor, current_account, require_roles
from ari.backoffice_api.dto import SegmentRequest, SegmentStatusRequest
from ari.content.domain.accounts import AccountContext, Role
from ari.content.domain.documents import (
    DocumentDeclaration,
    DocumentSegment,
    DocumentStatus,
    JobStatus,
    JobType,
    SegmentStatus,
)
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.geography import Land
from ari.domain.models import new_id

Owner = Annotated[AccountContext, Depends(require_roles(Role.OWNER))]
Anyone = Annotated[AccountContext, Depends(current_account)]
PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def _land(value: str | None) -> Land | None:
    if not value:
        return None
    try:
        return Land(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Land inconnu") from exc


def documents_router(services: Backoffice) -> APIRouter:
    router = APIRouter()
    content = services.content

    @router.post("/api/documents")
    async def upload(
        response: Response,
        account: Owner,
        file: Annotated[UploadFile, File()],
        provenance: Annotated[str, Form(min_length=1, max_length=2000)],
        consent_declaration: Annotated[str, Form(min_length=1, max_length=2000)],
        intended_use: Annotated[str, Form(max_length=500)] = "Entraînement FSP dans ARI",
        rights: Annotated[str, Form(pattern=r"^(unknown|incompatible|compatible)$")] = "unknown",
        rights_evidence: Annotated[str | None, Form(max_length=2000)] = None,
        land: Annotated[str | None, Form()] = None,
        city: Annotated[str | None, Form(max_length=120)] = None,
        exam_body: Annotated[str | None, Form(max_length=200)] = None,
        exam_date: Annotated[str | None, Form(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
        specialty: Annotated[str | None, Form(max_length=200)] = None,
    ) -> dict[str, Any]:
        limit = services.settings.content_upload_max_bytes
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail="Fichier trop volumineux")
        declaration = DocumentDeclaration(
            provenance=provenance,
            intended_use=intended_use,
            consent_declaration=consent_declaration,
            rights=rights,  # type: ignore[arg-type]
            rights_evidence=rights_evidence or None,
            land=_land(land),
            city=(city or "").strip() or None,
            exam_body=(exam_body or "").strip() or None,
            exam_date=exam_date or None,
            specialty=(specialty or "").strip() or None,
        )
        result = content.ingestion.ingest(
            data,
            file.filename or "document.pdf",
            declaration,
            actor=actor(account),
            via="backoffice",
        )
        response.status_code = 200 if result.duplicate else 201
        return {"document": jsonable_encoder(result.document), "duplicate": result.duplicate}

    @router.get("/api/documents")
    def list_documents(
        account: Anyone, status: str | None = None, land: str | None = None
    ) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            items = tx.list_documents(
                status=DocumentStatus(status) if status else None, land=_land(land)
            )
        return {"items": jsonable_encoder(items), "total": len(items)}

    @router.get("/api/documents/{document_id}")
    def document_detail(document_id: str, account: Anyone) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            document = tx.get_document(document_id)
            if document is None:
                raise NotFoundError("Document inconnu")
            segments = tx.segments(document_id)
            heads = {h.segment_id: h for h in tx.list_heads(document_id=document_id)}
        jobs = [j for j in content.queue.list() if j.document_id == document_id]
        return {
            "document": jsonable_encoder(document),
            "segments": [
                {
                    **jsonable_encoder(segment),
                    "protocol": (
                        {
                            "id": heads[segment.id].id,
                            "version": heads[segment.id].version,
                            "status": heads[segment.id].status.value,
                        }
                        if segment.id in heads
                        else None
                    ),
                }
                for segment in segments
            ],
            "jobs": {
                status.value: sum(1 for j in jobs if j.status is status) for status in JobStatus
            },
            "last_error": next((j.last_error for j in reversed(jobs) if j.last_error), None),
        }

    @router.get("/api/documents/{document_id}/pages/{number}")
    def page_text(document_id: str, number: int, account: Anyone) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            page = next((p for p in tx.pages(document_id) if p.page_number == number), None)
        if page is None:
            raise NotFoundError("Page inconnue")
        return {"page_number": page.page_number, "text": page.text}

    @router.get("/api/documents/{document_id}/pages/{number}/image")
    def page_image(document_id: str, number: int, account: Anyone) -> Response:
        with content.repository.transaction() as tx:
            document = tx.get_document(document_id)
        if document is None:
            raise NotFoundError("Document inconnu")
        try:
            png = content.extractor.render_page(content.storage.path(document.storage_key), number)
        except ValueError as exc:
            raise NotFoundError("Page inconnue") from exc
        return Response(content=png, media_type="image/png", headers=PRIVATE)

    @router.get("/api/documents/{document_id}/original")
    def original(document_id: str, account: Anyone) -> FileResponse:
        with content.repository.transaction() as tx:
            document = tx.get_document(document_id)
        if document is None:
            raise NotFoundError("Document inconnu")
        return FileResponse(
            content.storage.path(document.storage_key),
            media_type="application/pdf",
            filename=f"{document.id}.pdf",
            headers=PRIVATE,
        )

    @router.post("/api/documents/{document_id}/release")
    def release(document_id: str, account: Owner) -> dict[str, Any]:
        released = content.workflow.release_document(document_id, actor=actor(account))
        return {"released": released}

    @router.post("/api/documents/{document_id}/segments", status_code=201)
    def add_segment(document_id: str, body: SegmentRequest, account: Owner) -> dict[str, Any]:
        """A manual split: the owner fixes what the model missed or glued together."""
        if body.page_to < body.page_from:
            raise HTTPException(status_code=422, detail="Pages dans le mauvais ordre")
        with content.repository.transaction() as tx:
            document = tx.get_document(document_id)
            if document is None:
                raise NotFoundError("Document inconnu")
            if document.page_count is not None and body.page_to > document.page_count:
                raise HTTPException(status_code=422, detail="Page hors du document")
            existing = tx.segments(document_id)
            segment = DocumentSegment(
                id=new_id(),
                document_id=document_id,
                index=max((s.index for s in existing), default=-1) + 1,
                page_from=body.page_from,
                page_to=body.page_to,
                start_marker=body.start_marker,
                confidence=1.0,
                origin="manual",
            )
            tx.add_segments([segment])
        content.queue.enqueue(
            JobType.EXTRACT_PROTOCOL,
            {"document_id": document_id, "segment_id": segment.id},
            document_id=document_id,
        )
        return jsonable_encoder(segment)  # type: ignore[no-any-return]

    @router.patch("/api/segments/{segment_id}")
    def segment_status(
        segment_id: str, body: SegmentStatusRequest, account: Owner
    ) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            segment = tx.get_segment(segment_id)
            if segment is None:
                raise NotFoundError("Segment inconnu")
            if any(
                h.segment_id == segment_id for h in tx.list_heads(document_id=segment.document_id)
            ):
                raise InvalidStateError("Un protocole existe déjà pour ce segment")
            tx.set_segment_status(segment_id, SegmentStatus(body.status))
            updated = tx.get_segment(segment_id)
        if body.status == "pending":
            content.queue.enqueue(
                JobType.EXTRACT_PROTOCOL,
                {"document_id": segment.document_id, "segment_id": segment_id},
                document_id=segment.document_id,
            )
        return jsonable_encoder(updated)  # type: ignore[no-any-return]

    @router.get("/api/jobs")
    def list_jobs(account: Owner, status: str | None = None) -> dict[str, Any]:
        items = content.queue.list(JobStatus(status) if status else None)
        return {"items": jsonable_encoder(items), "total": len(items)}

    @router.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str, account: Owner) -> dict[str, Any]:
        return jsonable_encoder(content.queue.retry(job_id))  # type: ignore[no-any-return]

    @router.get("/api/meta/lands")
    def lands(request: Request) -> list[str]:
        return [land.value for land in Land]

    return router
