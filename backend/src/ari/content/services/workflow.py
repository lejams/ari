"""The protocol state machine: release, revise, doctor decision, owner decision, gold.

extracted → doctor_review → (changes_requested | doctor_approved) → (gold | rejected).
Every correction is a new version, every decision an append-only review on the exact hash,
every transition an event. All of it inside one transaction with the head row locked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from ari.content.domain.documents import Actor, ProtocolEvent, ProtocolVersion
from ari.content.domain.errors import ConflictError
from ari.content.domain.pii import PII_DETECTOR_VERSION, scan
from ari.content.domain.protocol import (
    Decision,
    GoldProtocol,
    ProtocolRecord,
    ProtocolReview,
    ProtocolStatus,
)
from ari.content.ports import ContentRepository, ContentTransaction
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import new_id

CLOSED = frozenset({ProtocolStatus.GOLD, ProtocolStatus.REJECTED, ProtocolStatus.SUPERSEDED})
DOCTOR_STAGES = frozenset({ProtocolStatus.DOCTOR_REVIEW, ProtocolStatus.CHANGES_REQUESTED})


def _account(actor: Actor) -> str:
    return actor.account_id or actor.name


class ProtocolWorkflow:
    def __init__(self, repository: ContentRepository) -> None:
        self._repository = repository

    def release(self, protocol_id: str, *, actor: Actor) -> ProtocolVersion:
        """Hand an extracted protocol to the doctors' queue."""
        with self._repository.transaction() as tx:
            head = self._head(tx, protocol_id)
            if head.status is not ProtocolStatus.EXTRACTED:
                raise ConflictError(f"Le protocole est déjà {head.status.value}")
            return self._move(tx, head, ProtocolStatus.DOCTOR_REVIEW, "released", actor)

    def release_document(self, document_id: str, *, actor: Actor) -> int:
        with self._repository.transaction() as tx:
            heads = tx.list_heads(status=ProtocolStatus.EXTRACTED, document_id=document_id)
            for head in heads:
                self._move(tx, head, ProtocolStatus.DOCTOR_REVIEW, "released", actor)
            return len(heads)

    def revise(
        self,
        protocol_id: str,
        *,
        base_version: int,
        base_hash: str,
        record: ProtocolRecord,
        actor: Actor,
        via: Literal["doctor", "owner"],
    ) -> ProtocolVersion:
        """A correction: new version, same status, the base becomes `superseded`."""
        with self._repository.transaction() as tx:
            head = self._head(tx, protocol_id)
            if (head.version, head.content_hash) != (base_version, base_hash):
                raise ConflictError("Une version plus récente existe ; rechargez le protocole")
            if head.status in CLOSED:
                raise ConflictError(f"Un protocole {head.status.value} ne se modifie plus")
            if record.source != head.record.source:
                raise InvalidStateError("La source du protocole ne se réécrit pas")
            if record.content_hash == head.content_hash:
                return head
            revised = ProtocolVersion(
                id=protocol_id,
                version=head.version + 1,
                record=record,
                document_id=head.document_id,
                segment_id=head.segment_id,
                created_via=via,
                status=head.status,
                pii_findings=scan(record.model_dump(mode="json")),
                pii_detector_version=PII_DETECTOR_VERSION,
                parent_version=head.version,
                created_by_account_id=actor.account_id,
            )
            tx.add_protocol(revised)
            tx.set_protocol_status(protocol_id, head.version, ProtocolStatus.SUPERSEDED)
            tx.add_event(
                ProtocolEvent(
                    id=new_id(),
                    protocol_id=protocol_id,
                    protocol_version=revised.version,
                    event_type="revised",
                    actor=actor,
                    payload={"parent_version": head.version, "via": via},
                )
            )
            return revised

    def doctor_decide(
        self,
        protocol_id: str,
        *,
        version: int,
        protocol_hash: str,
        decision: Decision,
        notes: str,
        actor: Actor,
    ) -> ProtocolVersion:
        with self._repository.transaction() as tx:
            head = self._exact(tx, protocol_id, version, protocol_hash)
            if head.status not in DOCTOR_STAGES:
                raise ConflictError(
                    f"Le protocole n'est pas en relecture médecin ({head.status.value})"
                )
            if decision == "approve":
                blockers = head.record.review_blockers("doctor")
                if blockers:
                    raise InvalidStateError("Approbation impossible : " + " ; ".join(blockers))
            tx.add_review(self._review(head, "doctor", decision, notes, actor, None))
            target = {
                "approve": ProtocolStatus.DOCTOR_APPROVED,
                "request_changes": ProtocolStatus.CHANGES_REQUESTED,
                "reject": ProtocolStatus.REJECTED,
            }[decision]
            return self._move(tx, head, target, "doctor_decision", actor, decision=decision)

    def owner_decide(
        self,
        protocol_id: str,
        *,
        version: int,
        protocol_hash: str,
        decision: Decision,
        notes: str,
        actor: Actor,
        pii_override_note: str | None = None,
    ) -> ProtocolVersion:
        with self._repository.transaction() as tx:
            head = self._exact(tx, protocol_id, version, protocol_hash)
            if head.status is not ProtocolStatus.DOCTOR_APPROVED:
                raise ConflictError(
                    f"Le protocole n'attend pas la validation propriétaire ({head.status.value})"
                )
            if decision == "approve":
                blockers = list(head.record.review_blockers("owner"))
                if head.pii_findings and not pii_override_note:
                    blockers.append(
                        f"{len(head.pii_findings)} détection(s) PII sans note de dérogation"
                    )
                document = tx.get_document(head.document_id)
                if document is None:
                    raise NotFoundError("Document inconnu")
                if document.declaration.rights == "incompatible":
                    blockers.append("Droits du document incompatibles")
                if blockers:
                    raise InvalidStateError("Validation impossible : " + " ; ".join(blockers))
                gold = GoldProtocol(
                    protocol_id=protocol_id,
                    protocol_version=head.version,
                    protocol_hash=head.content_hash,
                    location=head.record.location.as_case_location(),
                    record=head.record,
                    document_id=head.document_id,
                    rights=document.declaration.rights,
                    rights_evidence=document.declaration.rights_evidence,
                    frozen_at=datetime.now(UTC),
                    frozen_by_account_id=actor.account_id,
                )
                tx.add_gold(gold)
            tx.add_review(self._review(head, "owner", decision, notes, actor, pii_override_note))
            target = {
                "approve": ProtocolStatus.GOLD,
                "request_changes": ProtocolStatus.DOCTOR_REVIEW,
                "reject": ProtocolStatus.REJECTED,
            }[decision]
            event = "gold_frozen" if decision == "approve" else "owner_decision"
            return self._move(tx, head, target, event, actor, decision=decision)

    # ----- helpers ------------------------------------------------------------------------

    @staticmethod
    def _head(tx: ContentTransaction, protocol_id: str) -> ProtocolVersion:
        head = tx.head(protocol_id, lock=True)
        if head is None:
            raise NotFoundError("Protocole inconnu")
        return head

    def _exact(
        self, tx: ContentTransaction, protocol_id: str, version: int, protocol_hash: str
    ) -> ProtocolVersion:
        head = self._head(tx, protocol_id)
        if (head.version, head.content_hash) != (version, protocol_hash):
            raise ConflictError("La décision ne porte pas sur la version courante du protocole")
        return head

    @staticmethod
    def _review(
        head: ProtocolVersion,
        stage: Literal["doctor", "owner"],
        decision: Decision,
        notes: str,
        actor: Actor,
        pii_override_note: str | None,
    ) -> ProtocolReview:
        return ProtocolReview(
            id=new_id(),
            protocol_id=head.id,
            protocol_version=head.version,
            protocol_hash=head.content_hash,
            stage=stage,
            decision=decision,
            reviewer_account_id=_account(actor),
            reviewer_name=actor.name,
            reviewed_at=datetime.now(UTC),
            notes=notes,
            pii_override_note=pii_override_note,
        )

    @staticmethod
    def _move(
        tx: ContentTransaction,
        head: ProtocolVersion,
        status: ProtocolStatus,
        event_type: str,
        actor: Actor,
        **payload: object,
    ) -> ProtocolVersion:
        tx.set_protocol_status(head.id, head.version, status)
        tx.add_event(
            ProtocolEvent(
                id=new_id(),
                protocol_id=head.id,
                protocol_version=head.version,
                event_type=event_type,
                actor=actor,
                payload={"from": head.status.value, "to": status.value, **payload},
            )
        )
        moved = tx.get_protocol(head.id, head.version)
        assert moved is not None
        return moved
