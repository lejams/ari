"""Protocols: the review queue, one protocol with its diff and checklist, decisions, gold."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import ValidationError

from ari.backoffice_api.container import Backoffice
from ari.backoffice_api.deps import actor, current_account, require_roles
from ari.backoffice_api.dto import DecisionRequest, ReviseRequest
from ari.content.domain.accounts import AccountContext, Role
from ari.content.domain.documents import ProtocolVersion
from ari.content.domain.protocol import ProtocolRecord, ProtocolStatus
from ari.content.ports import ContentTransaction
from ari.content.services.diff import field_diff
from ari.domain.errors import NotFoundError
from ari.domain.geography import Land

Owner = Annotated[AccountContext, Depends(require_roles(Role.OWNER))]
Physician = Annotated[AccountContext, Depends(require_roles(Role.PHYSICIAN_REVIEWER))]
Reviser = Annotated[AccountContext, Depends(require_roles(Role.PHYSICIAN_REVIEWER, Role.OWNER))]
Anyone = Annotated[AccountContext, Depends(current_account)]


def _land(value: str | None) -> Land | None:
    if not value:
        return None
    try:
        return Land(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Land inconnu") from exc


def summary(head: ProtocolVersion) -> dict[str, Any]:
    record = head.record
    return {
        "id": head.id,
        "version": head.version,
        "content_hash": head.content_hash,
        "status": head.status.value,
        "document_id": head.document_id,
        "segment_id": head.segment_id,
        "page_from": head.page_from,
        "page_to": head.page_to,
        "land": record.location.land.value if record.location.land else None,
        "specialty": record.location.specialty,
        "title_hint": record.source.title_hint,
        "presenting_complaint_de": record.patient.presenting_complaint_de,
        "open_doctor_blockers": len(record.review_blockers("doctor")),
        "open_owner_blockers": len(record.review_blockers("owner")),
        "pii_findings": len(head.pii_findings),
        "difficulty": record.pedagogy.difficulty,
        "created_via": head.created_via,
        "created_at": head.created_at.isoformat(),
    }


def detail(tx: ContentTransaction, head: ProtocolVersion) -> dict[str, Any]:
    versions = tx.versions(head.id)
    previous = next((v for v in versions if v.version == head.parent_version), None)
    first = versions[0] if versions else None
    document = tx.get_document(head.document_id)
    return {
        **summary(head),
        "record": head.record.model_dump(mode="json"),
        "pii": [f.model_dump(mode="json") for f in head.pii_findings],
        "pii_detector_version": head.pii_detector_version,
        "blockers": {
            "doctor": head.record.review_blockers("doctor"),
            "owner": head.record.review_blockers("owner"),
        },
        "open_uncertainties": head.record.open_uncertainties,
        "diff_to_previous": jsonable_encoder(
            field_diff(previous.record.model_dump(mode="json"), head.record.model_dump(mode="json"))
            if previous
            else ()
        ),
        "diff_to_first": jsonable_encoder(
            field_diff(first.record.model_dump(mode="json"), head.record.model_dump(mode="json"))
            if first and first.version != head.version
            else ()
        ),
        "versions": [
            {
                "version": v.version,
                "status": v.status.value,
                "created_via": v.created_via,
                "created_by_account_id": v.created_by_account_id,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
        "reviews": [r.model_dump(mode="json") for r in tx.reviews(head.id)],
        "events": jsonable_encoder(tx.events(head.id)),
        "document": jsonable_encoder(document) if document else None,
        "gold": (g.model_dump(mode="json") if (g := tx.get_gold(head.id)) else None),
    }


def protocols_router(services: Backoffice) -> APIRouter:
    router = APIRouter()
    content = services.content

    def _head(tx: ContentTransaction, protocol_id: str) -> ProtocolVersion:
        head = tx.head(protocol_id)
        if head is None:
            raise NotFoundError("Protocole inconnu")
        return head

    @router.get("/api/protocols")
    def list_protocols(
        account: Anyone,
        status: str | None = None,
        land: str | None = None,
        document: str | None = None,
    ) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            heads = tx.list_heads(
                status=ProtocolStatus(status) if status else None,
                document_id=document,
                land=_land(land),
            )
        return {"items": [summary(h) for h in heads], "total": len(heads)}

    @router.get("/api/protocols/{protocol_id}")
    def protocol_detail(protocol_id: str, account: Anyone) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            return detail(tx, _head(tx, protocol_id))

    @router.get("/api/protocols/{protocol_id}/versions/{version}")
    def protocol_version(protocol_id: str, version: int, account: Anyone) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            found = tx.get_protocol(protocol_id, version)
            if found is None:
                raise NotFoundError("Version inconnue")
            return {**summary(found), "record": found.record.model_dump(mode="json")}

    @router.post("/api/protocols/{protocol_id}/versions", status_code=201)
    def revise(protocol_id: str, body: ReviseRequest, account: Reviser) -> dict[str, Any]:
        try:
            record = ProtocolRecord.model_validate_json(json.dumps(body.record))
        except ValidationError as exc:
            errors = [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
            ]
            raise HTTPException(status_code=422, detail=errors) from exc
        via: Literal["doctor", "owner"] = (
            "owner"
            if account.has(Role.OWNER) and not account.has(Role.PHYSICIAN_REVIEWER)
            else "doctor"
        )
        revised = content.workflow.revise(
            protocol_id,
            base_version=body.base_version,
            base_hash=body.base_hash,
            record=record,
            actor=actor(account),
            via=via,
        )
        with content.repository.transaction() as tx:
            return detail(tx, revised)

    @router.post("/api/protocols/{protocol_id}/decisions/doctor")
    def doctor_decision(
        protocol_id: str, body: DecisionRequest, account: Physician
    ) -> dict[str, Any]:
        moved = content.workflow.doctor_decide(
            protocol_id,
            version=body.version,
            protocol_hash=body.protocol_hash,
            decision=body.decision,
            notes=body.notes,
            actor=actor(account),
        )
        with content.repository.transaction() as tx:
            return detail(tx, moved)

    @router.post("/api/protocols/{protocol_id}/decisions/owner")
    def owner_decision(protocol_id: str, body: DecisionRequest, account: Owner) -> dict[str, Any]:
        moved = content.workflow.owner_decide(
            protocol_id,
            version=body.version,
            protocol_hash=body.protocol_hash,
            decision=body.decision,
            notes=body.notes,
            actor=actor(account),
            pii_override_note=body.pii_override_note,
        )
        with content.repository.transaction() as tx:
            return detail(tx, moved)

    @router.post("/api/protocols/{protocol_id}/release")
    def release(protocol_id: str, account: Owner) -> dict[str, Any]:
        moved = content.workflow.release(protocol_id, actor=actor(account))
        with content.repository.transaction() as tx:
            return detail(tx, moved)

    @router.delete("/api/protocols/{protocol_id}", status_code=204)
    def delete(protocol_id: str, account: Owner) -> None:
        content.workflow.delete_protocol(protocol_id, actor=actor(account))

    @router.get("/api/gold")
    def list_gold(account: Anyone, land: str | None = None) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            items = tx.list_gold(land=_land(land))
        return {
            "items": [
                {
                    "protocol_id": g.protocol_id,
                    "protocol_version": g.protocol_version,
                    "gold_hash": g.content_hash,
                    "land": g.location.land.value if g.location.land else None,
                    "city": g.location.city,
                    "specialty": g.location.specialty,
                    "exam_date": g.location.exam_date,
                    "presenting_complaint_de": g.record.patient.presenting_complaint_de,
                    "difficulty": g.record.pedagogy.difficulty,
                    "frozen_at": g.frozen_at.isoformat(),
                }
                for g in items
            ],
            "total": len(items),
        }

    @router.get("/api/gold/export")
    def export_gold(account: Owner) -> Response:
        with content.repository.transaction() as tx:
            lines = [g.model_dump_json() for g in tx.list_gold()]
        return Response(
            content="\n".join(lines) + ("\n" if lines else ""),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="ari-gold-protocols.jsonl"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/api/gold/{protocol_id}")
    def gold_detail(protocol_id: str, account: Anyone) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            gold = tx.get_gold(protocol_id)
        if gold is None:
            raise NotFoundError("Protocole gold inconnu")
        return gold.model_dump(mode="json")

    return router
