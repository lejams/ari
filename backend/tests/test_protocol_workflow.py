"""Release, correction, doctor and owner decisions, gold: the database keeps every step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from content_fixtures import synthetic_protocol_pdf
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_content_pipeline import OPERATOR, declaration, drain

from ari.content.container import ContentContainer
from ari.content.domain.documents import Actor, ProtocolVersion
from ari.content.domain.errors import ConflictError
from ari.content.domain.protocol import Pedagogy, Pitfall, ProtocolRecord, ProtocolStatus
from ari.content.services.diff import field_diff
from ari.domain.errors import InvalidStateError

DOCTOR = Actor(kind="account", name="Dr Test", account_id="doctor-1")
OWNER = Actor(kind="account", name="Owner", account_id="owner-1")


def settle(record: ProtocolRecord) -> ProtocolRecord:
    """Answer every open point the fake draft left, the way a doctor would in the UI."""
    raw = record.model_dump(mode="json")
    for item in raw["anamnesis"]:
        if item["polarity"] == "unknown":
            item.update(value_de="Keine bekannt", polarity="absent", uncertainty=None)
        item["uncertainty"] = None
    raw["patient"]["uncertainty"] = None
    for question in raw["unresolved_questions"]:
        question["answer"] = "Oui"
    for uncertainty in raw["field_uncertainties"]:
        uncertainty["resolution"] = "Vérifié"
    raw["pedagogy"] = Pedagogy(
        critical_pitfalls=(Pitfall(text_fr="Oublier les allergies", related_item_ids=("a01",)),),
        difficulty="mittel",
    ).model_dump(mode="json")
    return ProtocolRecord.model_validate_json(json.dumps(raw))


@pytest.fixture
def extracted(content_container: ContentContainer, tmp_path: Path) -> ProtocolVersion:
    pdf = synthetic_protocol_pdf(tmp_path / "one.pdf", count=1)
    content_container.ingestion.ingest(
        pdf.read_bytes(), pdf.name, declaration(), actor=OPERATOR, via="cli"
    )
    drain(content_container)
    with content_container.repository.transaction() as tx:
        return tx.list_heads()[0]


def test_state_machine_from_extraction_to_gold(
    content_container: ContentContainer, extracted: ProtocolVersion
) -> None:
    workflow = content_container.workflow
    released = workflow.release(extracted.id, actor=OPERATOR)
    assert released.status is ProtocolStatus.DOCTOR_REVIEW
    with pytest.raises(ConflictError):
        workflow.release(extracted.id, actor=OPERATOR)

    # The draft still has open points: the doctor cannot approve it as is.
    with pytest.raises(InvalidStateError, match="Approbation impossible"):
        workflow.doctor_decide(
            extracted.id,
            version=1,
            protocol_hash=extracted.content_hash,
            decision="approve",
            notes="",
            actor=DOCTOR,
        )
    settled = settle(extracted.record)
    revised = workflow.revise(
        extracted.id,
        base_version=1,
        base_hash=extracted.content_hash,
        record=settled,
        actor=DOCTOR,
        via="doctor",
    )
    assert (revised.version, revised.status, revised.parent_version) == (
        2,
        ProtocolStatus.DOCTOR_REVIEW,
        1,
    )
    with pytest.raises(ConflictError, match="plus récente"):
        workflow.revise(
            extracted.id,
            base_version=1,
            base_hash=extracted.content_hash,
            record=settled,
            actor=DOCTOR,
            via="doctor",
        )
    changes = field_diff(extracted.record.model_dump(mode="json"), settled.model_dump(mode="json"))
    assert any(c.path == "pedagogy.difficulty" and c.after == "mittel" for c in changes)

    approved = workflow.doctor_decide(
        extracted.id,
        version=2,
        protocol_hash=revised.content_hash,
        decision="approve",
        notes="Relu.",
        actor=DOCTOR,
    )
    assert approved.status is ProtocolStatus.DOCTOR_APPROVED
    with pytest.raises(ConflictError):  # the owner decides on the exact current version only
        workflow.owner_decide(
            extracted.id,
            version=1,
            protocol_hash=extracted.content_hash,
            decision="approve",
            notes="",
            actor=OWNER,
        )
    gold = workflow.owner_decide(
        extracted.id,
        version=2,
        protocol_hash=revised.content_hash,
        decision="approve",
        notes="Validé.",
        actor=OWNER,
    )
    assert gold.status is ProtocolStatus.GOLD
    with content_container.repository.transaction() as tx:
        frozen = tx.get_gold(extracted.id)
        assert frozen is not None and frozen.protocol_hash == revised.content_hash
        assert frozen.location.land is not None and frozen.frozen_by_account_id == "owner-1"
        assert [v.status for v in tx.versions(extracted.id)] == [
            ProtocolStatus.SUPERSEDED,
            ProtocolStatus.GOLD,
        ]
        assert [(r.stage, r.decision) for r in tx.reviews(extracted.id)] == [
            ("doctor", "approve"),
            ("owner", "approve"),
        ]
        assert [e.event_type for e in tx.events(extracted.id)] == [
            "extracted",
            "released",
            "revised",
            "doctor_decision",
            "gold_frozen",
        ]
        assert tx.list_gold()[0].protocol_id == extracted.id
    # Gold and reviews are frozen by the database itself.
    engine = content_container.repository.engine  # type: ignore[attr-defined]
    for sql in ("UPDATE gold_protocols SET land='Berlin'", "DELETE FROM protocol_reviews"):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(sql))
    with pytest.raises(ConflictError, match="ne se modifie plus"):
        workflow.revise(
            extracted.id,
            base_version=2,
            base_hash=revised.content_hash,
            record=settled,
            actor=DOCTOR,
            via="doctor",
        )


def test_owner_sends_back_and_rejects(
    content_container: ContentContainer, extracted: ProtocolVersion
) -> None:
    workflow = content_container.workflow
    workflow.release(extracted.id, actor=OPERATOR)
    revised = workflow.revise(
        extracted.id,
        base_version=1,
        base_hash=extracted.content_hash,
        record=settle(extracted.record),
        actor=DOCTOR,
        via="doctor",
    )
    workflow.doctor_decide(
        extracted.id,
        version=2,
        protocol_hash=revised.content_hash,
        decision="approve",
        notes="",
        actor=DOCTOR,
    )
    back = workflow.owner_decide(
        extracted.id,
        version=2,
        protocol_hash=revised.content_hash,
        decision="request_changes",
        notes="Préciser le motif",
        actor=OWNER,
    )
    assert back.status is ProtocolStatus.DOCTOR_REVIEW
    rejected = workflow.doctor_decide(
        extracted.id,
        version=2,
        protocol_hash=revised.content_hash,
        decision="reject",
        notes="Doublon",
        actor=DOCTOR,
    )
    assert rejected.status is ProtocolStatus.REJECTED
    with content_container.repository.transaction() as tx:
        assert tx.get_gold(extracted.id) is None
