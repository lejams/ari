"""The `generate_bundle_draft` job: gold protocol → skeleton → model → validated draft row.

Requesting a draft only enqueues a job; the worker runs it. A model failure is left to the
worker's retry policy (`ModelCallFailed`); an output that does not fit the skeleton is not a
failure but an `invalid` draft, stored with its reasons so the owner can read them and retry.
"""

from __future__ import annotations

import json

from ari.content.domain.bundles import (
    BundleDraft,
    BundleDraftStatus,
    BundleVariantRequest,
    ScenarioRef,
)
from ari.content.domain.documents import Actor, Job, JobType
from ari.content.ports import ContentRepository, JobQueue
from ari.content.services.ai import ContentModel
from ari.content.services.bundle_generator import (
    BundleDraftInvalid,
    assemble,
    available_phases,
    build_skeleton,
    model_payload,
)
from ari.domain.errors import NotFoundError
from ari.domain.models import new_id


class BundleDrafting:
    def __init__(self, repository: ContentRepository, model: ContentModel, queue: JobQueue) -> None:
        self._repository = repository
        self._model = model
        self._queue = queue

    def request(self, gold_protocol_id: str, request: BundleVariantRequest, *, actor: Actor) -> Job:
        """Validate the request against the gold protocol, then enqueue the generation."""
        with self._repository.transaction() as tx:
            gold = tx.get_gold(gold_protocol_id)
        if gold is None:
            raise NotFoundError("Protocole gold inconnu")
        build_skeleton(gold, request)  # Refuses phases the protocol cannot support.
        return self._queue.enqueue(
            JobType.GENERATE_BUNDLE_DRAFT,
            {
                "gold_protocol_id": gold_protocol_id,
                "request": request.model_dump(mode="json"),
                "created_by_account_id": actor.account_id,
            },
            document_id=gold.document_id,
            protocol_id=gold_protocol_id,
        )

    async def run(self, job: Job) -> None:
        gold_protocol_id = str(job.payload["gold_protocol_id"])
        request = BundleVariantRequest.model_validate_json(json.dumps(job.payload["request"]))
        created_by = job.payload.get("created_by_account_id")
        with self._repository.transaction() as tx:
            gold = tx.get_gold(gold_protocol_id)
            if gold is None:
                raise NotFoundError("Protocole gold inconnu")
            document = tx.get_document(gold.document_id)
            if document is None:
                raise NotFoundError("Document du protocole gold inconnu")
        skeleton = build_skeleton(gold, request)
        output, run = await self._model.draft_bundle(
            job_id=job.id,
            document_id=gold.document_id,
            protocol_id=gold_protocol_id,
            payload=model_payload(skeleton, gold, request),
        )
        try:
            bundle = assemble(skeleton, gold, document, request, output)
        except BundleDraftInvalid as exc:
            draft = BundleDraft(
                id=new_id(),
                gold_protocol_id=gold_protocol_id,
                gold_hash=gold.content_hash,
                request=request,
                status=BundleDraftStatus.INVALID,
                bundle=None,
                bundle_hash=None,
                case_id=None,
                case_version=None,
                validation_errors=exc.errors,
                ai_run_id=run.id,
                created_by_account_id=str(created_by) if created_by else None,
            )
        else:
            case = bundle.cases[0]
            draft = BundleDraft(
                id=new_id(),
                gold_protocol_id=gold_protocol_id,
                gold_hash=gold.content_hash,
                request=request,
                status=BundleDraftStatus.DRAFT,
                bundle=bundle.model_dump(mode="json"),
                bundle_hash=bundle.content_hash,
                case_id=case.id,
                case_version=case.version,
                scenario_refs=tuple(
                    ScenarioRef(id=s.id, version=s.version, phase=s.phase)  # type: ignore[arg-type]
                    for s in bundle.scenarios
                ),
                ai_run_id=run.id,
                created_by_account_id=str(created_by) if created_by else None,
            )
        with self._repository.transaction() as tx:
            tx.add_ai_run(run)
            tx.add_bundle_draft(draft)


__all__ = ["BundleDrafting", "available_phases"]
