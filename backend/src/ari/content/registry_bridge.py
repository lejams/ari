"""From a validated bundle draft to the platform registry, driven by back-office accounts.

The registry (`ClinicalStore`, platform database) keeps its own rules: import is idempotent on
identical hashes, two human approvals on the exact content precede publication, publication is
audited. This bridge adds what only the back-office knows: which account acts, which roles it
holds, and that the case really derives from a frozen gold protocol of the content database.

Two databases, no distributed transaction: a crash between the registry import and the draft's
status update is repaired by importing again (identical hashes are a no-op for the registry).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from ari.content.domain.accounts import AccountContext, Role
from ari.content.domain.bundles import BundleDraft, BundleDraftStatus
from ari.content.ports import ContentRepository
from ari.domain.clinical import CaseReview, ClinicalBundle
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import new_id
from ari.infrastructure.cases.clinical_store import ClinicalStore

ReviewType = Literal["clinical", "linguistic"]
ReviewDecision = Literal["approve", "request_changes", "reject"]
REVIEW_ROLES: dict[ReviewType, Role] = {
    "clinical": Role.PHYSICIAN_REVIEWER,
    "linguistic": Role.LINGUISTIC_REVIEWER,
}


def declared_actor(account: AccountContext) -> str:
    return f"{account.display_name} [{account.id}]"


class RegistryBridge:
    def __init__(self, store: ClinicalStore, repository: ContentRepository) -> None:
        self._store = store
        self._repository = repository

    def import_draft(self, draft_id: str, *, account: AccountContext) -> BundleDraft:
        self._require(account, Role.OWNER)
        with self._repository.transaction() as tx:
            draft = tx.get_bundle_draft(draft_id, lock=True)
            if draft is None:
                raise NotFoundError("Brouillon inconnu")
            if draft.status is BundleDraftStatus.INVALID or draft.bundle is None:
                raise InvalidStateError("Un brouillon invalide ne s'importe pas")
            bundle = ClinicalBundle.model_validate_json(json.dumps(draft.bundle))
            gold = tx.get_gold(draft.gold_protocol_id)
            self._check_provenance(bundle, gold.protocol_hash if gold else None)
            self._store.import_bundle(bundle)
            if draft.status is BundleDraftStatus.DRAFT:
                tx.mark_bundle_draft_imported(draft.id)
            imported = tx.get_bundle_draft(draft.id)
            assert imported is not None
            return imported

    @staticmethod
    def _check_provenance(bundle: ClinicalBundle, gold_protocol_hash: str | None) -> None:
        """Every non-synthetic case must point at the frozen record of its gold protocol."""
        for case in bundle.cases:
            source = next(s for s in bundle.sources if s.id == case.protocol_source_id)
            if source.synthetic:
                continue
            if gold_protocol_hash is None or case.gold_protocol.protocol_hash != gold_protocol_hash:
                raise InvalidStateError(
                    f"Le cas {case.id} ne trace pas vers un protocole gold figé de la base content"
                )

    def list_scenarios(self, status: str | None = None) -> list[dict[str, Any]]:
        return self._store.list_scenarios(status)

    def inspect(self, scenario_id: str, version: str) -> dict[str, Any]:
        return self._store.inspect(scenario_id, version)

    def record_review(
        self,
        scenario_id: str,
        version: str,
        *,
        review_type: ReviewType,
        decision: ReviewDecision,
        notes: str,
        account: AccountContext,
    ) -> CaseReview:
        self._require(account, REVIEW_ROLES[review_type])
        report = self._store.inspect(scenario_id, version)
        if decision == "approve":
            other = "linguistic" if review_type == "clinical" else "clinical"
            for previous in report["reviews"]:
                if (
                    previous.get("reviewer_account_id") == account.id
                    and previous["review_type"] == other
                    and previous["decision"] == "approve"
                    and previous["scenario_hash"] == report["scenario_hash"]
                ):
                    raise InvalidStateError(
                        "Le même compte ne peut pas donner les deux approbations d'un contenu"
                    )
        bundle = report["bundle"]
        review = CaseReview.model_validate_json(
            json.dumps(
                {
                    "id": new_id(),
                    "case": {
                        "id": bundle["cases"][0]["id"],
                        "version": bundle["cases"][0]["version"],
                    },
                    "case_hash": report["case_hash"],
                    "scenario": {"id": scenario_id, "version": version},
                    "scenario_hash": report["scenario_hash"],
                    "review_type": review_type,
                    "reviewer_name": account.display_name,
                    "reviewer_account_id": account.id,
                    "reviewed_at": datetime.now(UTC).isoformat(),
                    "decision": decision,
                    "notes": notes,
                }
            )
        )
        self._store.record_review(review)
        return review

    def publish(self, scenario_id: str, version: str, *, account: AccountContext) -> None:
        self._require(account, Role.OWNER)
        self._store.publish(scenario_id, version, actor=declared_actor(account))

    def withdraw(self, scenario_id: str, version: str, *, account: AccountContext) -> None:
        self._require(account, Role.OWNER)
        self._store.withdraw(scenario_id, version, actor=declared_actor(account))

    @staticmethod
    def _require(account: AccountContext, role: Role) -> None:
        if not account.has(role):
            raise PermissionError(f"Rôle requis : {role.value}")
