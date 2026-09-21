"""Gold → bundle drafts → platform registry: generation, import, reviews, publication."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from ari.backoffice_api.container import Backoffice
from ari.backoffice_api.deps import actor, current_account, require_roles
from ari.backoffice_api.dto import BundleDraftRequest, RegistryReviewRequest
from ari.content.domain.accounts import AccountContext, Role
from ari.content.domain.bundles import BundleDraft, BundleVariantRequest
from ari.content.domain.documents import Job, JobStatus, JobType
from ari.content.services.bundle_generator import available_phases
from ari.domain.errors import NotFoundError
from ari.infrastructure.cases.report import review_markdown

Owner = Annotated[AccountContext, Depends(require_roles(Role.OWNER))]
Reviewer = Annotated[
    AccountContext,
    Depends(require_roles(Role.PHYSICIAN_REVIEWER, Role.LINGUISTIC_REVIEWER)),
]
Anyone = Annotated[AccountContext, Depends(current_account)]
PENDING = frozenset({JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED, JobStatus.DEAD})


def draft_summary(draft: BundleDraft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "gold_protocol_id": draft.gold_protocol_id,
        "gold_hash": draft.gold_hash,
        "request": draft.request.model_dump(mode="json"),
        "status": draft.status.value,
        "bundle_hash": draft.bundle_hash,
        "case_id": draft.case_id,
        "case_version": draft.case_version,
        "scenario_refs": [
            {"id": ref.id, "version": ref.version, "phase": ref.phase}
            for ref in draft.scenario_refs
        ],
        "validation_errors": list(draft.validation_errors),
        "ai_run_id": draft.ai_run_id,
        "created_by_account_id": draft.created_by_account_id,
        "created_at": draft.created_at.isoformat(),
        "imported_at": draft.imported_at.isoformat() if draft.imported_at else None,
    }


def _job(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status.value,
        "attempts": job.attempts,
        "last_error": job.last_error,
        "request": job.payload.get("request"),
        "created_at": job.created_at.isoformat(),
    }


def bundles_router(services: Backoffice) -> APIRouter:
    router = APIRouter()
    content = services.content
    registry = services.registry

    @router.get("/api/gold/{protocol_id}/bundle-drafts")
    def list_drafts(protocol_id: str, account: Anyone) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            gold = tx.get_gold(protocol_id)
            if gold is None:
                raise NotFoundError("Protocole gold inconnu")
            drafts = tx.list_bundle_drafts(gold_protocol_id=protocol_id)
        jobs = [
            _job(job)
            for job in content.queue.list()
            if job.type is JobType.GENERATE_BUNDLE_DRAFT
            and job.protocol_id == protocol_id
            and job.status in PENDING
        ]
        return {
            "items": [draft_summary(d) for d in drafts],
            "total": len(drafts),
            "jobs": jobs,
            "available_phases": list(available_phases(gold)),
            "next_revision": max((d.request.revision for d in drafts), default=0) + 1,
        }

    @router.post("/api/gold/{protocol_id}/bundle-drafts", status_code=202)
    def request_draft(protocol_id: str, body: BundleDraftRequest, account: Owner) -> dict[str, Any]:
        request = BundleVariantRequest.model_validate_json(body.model_dump_json())
        job = content.drafting.request(protocol_id, request, actor=actor(account))
        return _job(job)

    @router.get("/api/bundle-drafts/{draft_id}")
    def draft_detail(draft_id: str, account: Anyone) -> dict[str, Any]:
        with content.repository.transaction() as tx:
            draft = tx.get_bundle_draft(draft_id)
        if draft is None:
            raise NotFoundError("Brouillon inconnu")
        return {**draft_summary(draft), "bundle": draft.bundle}

    @router.post("/api/bundle-drafts/{draft_id}/import")
    def import_draft(draft_id: str, account: Owner) -> dict[str, Any]:
        return draft_summary(registry.import_draft(draft_id, account=account))

    @router.get("/api/registry/scenarios")
    def list_scenarios(account: Anyone, status: str | None = None) -> dict[str, Any]:
        items = registry.list_scenarios(status)
        return {"items": items, "total": len(items)}

    @router.get("/api/registry/scenarios/{scenario_id}/{version}")
    def scenario_detail(scenario_id: str, version: str, account: Anyone) -> dict[str, Any]:
        report = registry.inspect(scenario_id, version)
        return {**jsonable_encoder(report), "markdown": review_markdown(report)}

    @router.post("/api/registry/scenarios/{scenario_id}/{version}/reviews", status_code=201)
    def review(
        scenario_id: str, version: str, body: RegistryReviewRequest, account: Reviewer
    ) -> dict[str, Any]:
        try:
            recorded = registry.record_review(
                scenario_id,
                version,
                review_type=body.review_type,
                decision=body.decision,
                notes=body.notes,
                account=account,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return recorded.model_dump(mode="json")

    @router.post("/api/registry/scenarios/{scenario_id}/{version}/publish")
    def publish(scenario_id: str, version: str, account: Owner) -> dict[str, Any]:
        registry.publish(scenario_id, version, account=account)
        return jsonable_encoder(registry.inspect(scenario_id, version))

    @router.post("/api/registry/scenarios/{scenario_id}/{version}/withdraw")
    def withdraw(scenario_id: str, version: str, account: Owner) -> dict[str, Any]:
        registry.withdraw(scenario_id, version, account=account)
        return jsonable_encoder(registry.inspect(scenario_id, version))

    return router
