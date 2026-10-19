"""Optional rights evidence remains traceable through bundle drafting."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

import pytest
from content_fixtures import DOCUMENT_ID, settled_record, synthetic_gold

from ari.content.domain.bundles import BundleDraft, BundleDraftStatus, BundleVariantRequest
from ari.content.domain.documents import (
    Actor,
    AiRun,
    Document,
    DocumentDeclaration,
    Job,
    JobType,
    ProtocolVersion,
)
from ari.content.domain.protocol import GoldProtocol, ProtocolStatus
from ari.content.fake_handlers import bundle_draft
from ari.content.schemas import BundleDraftOutput
from ari.content.services.bundle_drafting import BundleDrafting
from ari.content.services.workflow import ProtocolWorkflow
from ari.domain.clinical import ClinicalBundle


class Transaction:
    def __init__(self, gold: GoldProtocol, document: Document) -> None:
        self.gold = gold
        self.document = document
        self.drafts: list[BundleDraft] = []
        self.runs: list[AiRun] = []

    def get_gold(self, protocol_id: str) -> GoldProtocol | None:
        return self.gold if protocol_id == self.gold.protocol_id else None

    def get_document(self, document_id: str) -> Document | None:
        return self.document if document_id == self.document.id else None

    def add_bundle_draft(self, draft: BundleDraft) -> None:
        self.drafts.append(draft)

    def add_ai_run(self, run: AiRun) -> None:
        self.runs.append(run)


class Repository:
    def __init__(self, transaction: Transaction) -> None:
        self._transaction = transaction

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        yield self._transaction


class OwnerTransaction(Transaction):
    def __init__(self, document: Document) -> None:
        super().__init__(synthetic_gold().model_copy(update={"rights_evidence": None}), document)
        self.head_value = ProtocolVersion(
            id="P-cccccccc-000",
            version=2,
            record=settled_record(),
            document_id=document.id,
            segment_id="segment-1",
            created_via="doctor",
            status=ProtocolStatus.DOCTOR_APPROVED,
            pii_findings=(),
            pii_detector_version="test",
        )
        self.frozen: GoldProtocol | None = None

    def head(self, protocol_id: str, *, lock: bool = False) -> ProtocolVersion:
        del protocol_id, lock
        return self.head_value

    def add_gold(self, gold: GoldProtocol) -> None:
        self.frozen = gold

    def add_review(self, review: object) -> None:
        del review

    def set_protocol_status(self, protocol_id: str, version: int, status: ProtocolStatus) -> None:
        assert (protocol_id, version) == (self.head_value.id, self.head_value.version)
        self.head_value = replace(self.head_value, status=status)

    def add_event(self, event: object) -> None:
        del event

    def get_protocol(self, protocol_id: str, version: int) -> ProtocolVersion:
        assert (protocol_id, version) == (self.head_value.id, self.head_value.version)
        return self.head_value


class Queue:
    def __init__(self) -> None:
        self.enqueues = 0

    def enqueue(self, type: JobType, payload: dict[str, object], **kwargs: object) -> Job:
        self.enqueues += 1
        return Job(id="job-1", type=type, payload=payload, **kwargs)


class Model:
    def __init__(self) -> None:
        self.calls = 0

    async def draft_bundle(self, **kwargs: object) -> tuple[BundleDraftOutput, AiRun]:
        self.calls += 1
        payload = kwargs["payload"]
        assert isinstance(payload, dict)
        return (
            BundleDraftOutput.model_validate(bundle_draft(payload)),
            AiRun(
                id="run-1", operation="bundle_draft", provider="fake", model="fake",
                schema_name="BundleDraftOutput", input_hash="a" * 64, status="succeeded",
                latency_ms=0, usage={}, job_id="job-1",
            ),
        )


def optional_evidence_document() -> Document:
    return Document(
        id=DOCUMENT_ID,
        filename="protocol.pdf",
        size_bytes=1,
        storage_key="documents/test.pdf",
        uploaded_via="cli",
        declaration=DocumentDeclaration(
            provenance="Authored by uploader", intended_use="tests", consent_declaration="approved",
            rights="compatible", rights_evidence=None,
        ),
    )


@pytest.mark.asyncio
async def test_missing_evidence_allows_gold_bundle_request_and_generation() -> None:
    gold = synthetic_gold().model_copy(update={"rights_evidence": None})
    transaction = Transaction(gold, optional_evidence_document())
    queue = Queue()
    model = Model()
    drafting = BundleDrafting(Repository(transaction), model, queue)  # type: ignore[arg-type]
    request = BundleVariantRequest(phases=("arzt_patient", "arzt_arzt", "fachbegriffe"))

    job = drafting.request(gold.protocol_id, request, actor=Actor(kind="account", name="Owner"))
    await drafting.run(job)

    assert queue.enqueues == 1 and model.calls == 1
    assert len(transaction.drafts) == 1 and len(transaction.runs) == 1
    draft = transaction.drafts[0]
    assert draft.status is BundleDraftStatus.DRAFT and draft.bundle is not None
    assert draft.gold_protocol_id == gold.protocol_id and draft.gold_hash == gold.content_hash
    assert len(draft.scenario_refs) == 3
    bundle = ClinicalBundle.model_validate_json(json.dumps(draft.bundle))
    assert len(bundle.cases) == 1 and len(bundle.scenarios) == 3
    assert bundle.cases[0].gold_protocol.protocol_id == gold.protocol_id
    assert bundle.sources[0].rights == "compatible" and bundle.sources[0].rights_evidence is None


def test_missing_evidence_allows_owner_to_freeze_gold() -> None:
    transaction = OwnerTransaction(optional_evidence_document())
    workflow = ProtocolWorkflow(Repository(transaction))  # type: ignore[arg-type]
    head = transaction.head_value

    result = workflow.owner_decide(
        head.id,
        version=head.version,
        protocol_hash=head.content_hash,
        decision="approve",
        notes="",
        actor=Actor(kind="account", name="Owner", account_id="owner-1"),
    )

    assert result.status is ProtocolStatus.GOLD
    assert transaction.frozen is not None and transaction.frozen.rights_evidence is None
