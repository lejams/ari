"""Gold protocol → bundle: deterministic skeleton, strict assembly, drafts through the worker."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from content_fixtures import (
    DOCUMENT_ID,
    settled_record,
    synthetic_gold,
    synthetic_protocol_pdf,
)
from test_content_pipeline import OPERATOR, declaration, drain
from test_protocol_workflow import settle

from ari.content.container import ContentContainer, build_content_container
from ari.content.domain.bundles import BundleDraftStatus, BundleVariantRequest
from ari.content.domain.documents import Actor, Document, JobStatus
from ari.content.domain.protocol import Fachbegriff, GoldProtocol, ProtocolRecord
from ari.content.fake_handlers import FAKE_CONTENT_HANDLERS, bundle_draft
from ari.content.schemas import BundleDraftOutput
from ari.content.services.bundle_generator import (
    BundleDraftInvalid,
    assemble,
    available_phases,
    build_skeleton,
    model_payload,
)
from ari.domain.clinical import ClinicalBundle
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.infrastructure.providers.fake import FakeLLMProvider

ALL_PHASES = BundleVariantRequest(phases=("arzt_patient", "arzt_arzt", "fachbegriffe"))


def document() -> Document:
    return Document(
        id=DOCUMENT_ID,
        filename="protokolle.pdf",
        size_bytes=1000,
        storage_key=f"documents/cc/{DOCUMENT_ID}.pdf",
        uploaded_via="cli",
        declaration=declaration(),
        created_at=datetime(2026, 9, 18, tzinfo=UTC),
        updated_at=datetime(2026, 9, 18, tzinfo=UTC),
    )


def fake_output(gold: GoldProtocol, request: BundleVariantRequest) -> BundleDraftOutput:
    skeleton = build_skeleton(gold, request)
    return BundleDraftOutput.model_validate(bundle_draft(model_payload(skeleton, gold, request)))


def test_skeleton_is_derived_from_the_protocol_alone() -> None:
    gold = synthetic_gold()
    assert available_phases(gold) == ("arzt_patient", "arzt_arzt", "fachbegriffe")
    skeleton = build_skeleton(gold, ALL_PHASES)
    assert skeleton.case_id == "FSP-BY-P-cccccccc-000"
    assert skeleton.fact_ids == ("a01", "a02")
    assert [fact.critical for fact in skeleton.facts] == [False, True]  # a02 is a pitfall
    assert [q.id for q in skeleton.arzt_arzt_questions] == ["presentation", "q-q01"]
    assert [q.id for q in skeleton.fachbegriffe_questions] == ["term-t01"]
    payload = model_payload(skeleton, gold, ALL_PHASES)
    assert payload["persona_variant"] == "standard" and payload["critical_pitfalls_fr"]
    assert "frozen_by_account_id" not in payload and "source" not in payload


def test_protocol_without_asked_terms_cannot_make_a_lexicon_phase() -> None:
    record = settled_record()
    without = ProtocolRecord.model_validate_json(
        record.model_copy(
            update={"fachbegriffe": (Fachbegriff(id="t01", german="Dyspnoe", asked=False),)}
        ).model_dump_json()
    )
    gold = synthetic_gold(without)
    assert available_phases(gold) == ("arzt_patient", "arzt_arzt")
    with pytest.raises(InvalidStateError, match="fachbegriffe"):
        build_skeleton(gold, BundleVariantRequest(phases=("fachbegriffe",)))


def test_assembly_builds_a_valid_traced_bundle() -> None:
    gold = synthetic_gold()
    skeleton = build_skeleton(gold, ALL_PHASES)
    bundle = assemble(skeleton, gold, document(), ALL_PHASES, fake_output(gold, ALL_PHASES))
    assert isinstance(bundle, ClinicalBundle)
    case = bundle.cases[0]
    assert (case.id, case.version) == ("FSP-BY-P-cccccccc-000", "1")
    assert case.gold_protocol.protocol_hash == gold.protocol_hash
    assert case.location == gold.location and case.location.land is not None
    assert case.protocol_source_id == bundle.sources[0].id
    assert bundle.sources[0].source_type == "gold_protocol" and not bundle.sources[0].synthetic
    assert bundle.sources[0].rights == "compatible"
    assert {f.id: f.criticality for f in case.facts} == {"a01": "normal", "a02": "critical"}
    assert {f.id: f.disclosure.value for f in case.facts} == {
        "a01": "spontaneous",  # named in the opening
        "a02": "when_asked",
    }
    items = {item.id for item in case.assessment_items}
    assert items == {
        "section-aktuelle_beschwerden",
        "section-allergien",
        "critical-facts",
        "greeting",
        "introduction",
        "closing",
    }
    assert case.blockers == ()
    by_phase = {scenario.phase: scenario for scenario in bundle.scenarios}
    assert set(by_phase) == {"arzt_patient", "arzt_arzt", "fachbegriffe"}
    voice = by_phase["arzt_patient"]
    assert [s.id for s in voice.anamnesis_sections] == ["aktuelle_beschwerden", "allergien"]
    assert voice.difficulty == "mittel" and voice.cefr == "B2"
    presentation = by_phase["arzt_arzt"].practice
    assert presentation is not None and presentation.questions[0].kind == "presentation"
    lexicon = by_phase["fachbegriffe"].practice
    assert lexicon is not None and lexicon.questions[0].term_id == "t01"
    # The protocol's own lay wording is always an accepted answer.
    assert "Atemnot" in lexicon.assessment_items[0].accepted_answers
    # The same request yields the same bundle: regeneration is idempotent on identical output.
    again = assemble(skeleton, gold, document(), ALL_PHASES, fake_output(gold, ALL_PHASES))
    assert again.content_hash == bundle.content_hash


def test_assembly_refuses_unknown_missing_or_personal_content() -> None:
    gold = synthetic_gold()
    skeleton = build_skeleton(gold, ALL_PHASES)
    output = fake_output(gold, ALL_PHASES)
    raw = output.model_dump()
    raw["facts"][0]["fact_id"] = "a99"
    raw["opening_fact_ids"] = ["a01", "a01"]
    raw["practice_answers"].pop()
    with pytest.raises(BundleDraftInvalid) as unknown:
        assemble(skeleton, gold, document(), ALL_PHASES, BundleDraftOutput.model_validate(raw))
    assert unknown.value.errors == (
        "fait inconnu: a99",
        "fait sans phrases patient: a01",
        "faits d'ouverture en double",
        "question sans réponses: term-t01",
    )
    leaking = output.model_dump()
    leaking["facts"][0]["patient_phrases_de"] = ["Schreiben Sie mir: patientin@example.org"]
    with pytest.raises(BundleDraftInvalid) as personal:
        assemble(skeleton, gold, document(), ALL_PHASES, BundleDraftOutput.model_validate(leaking))
    assert any("Coordonnées personnelles" in error for error in personal.value.errors)


def test_assembly_refuses_identifying_details_the_model_wrote() -> None:
    gold = synthetic_gold()
    skeleton = build_skeleton(gold, ALL_PHASES)
    clean = fake_output(gold, ALL_PHASES)
    assemble(skeleton, gold, document(), ALL_PHASES, clean)  # the deterministic fake is clean
    cases = {
        "person": "Mein Hausarzt Dr. med. Weber hat mir das verschrieben.",
        "exact_date": "Es begann am 14.03.2026 abends.",
        "address": "Ich wohne in der Lindenstraße 12.",
        "postcode_city": "Ich komme aus 80331 München.",
        "institution": "Ich war im Klinikum Großhadern.",
    }
    for kind, sentence in cases.items():
        dirty = clean.model_dump()
        dirty["facts"][0]["patient_phrases_de"] = [sentence]
        with pytest.raises(BundleDraftInvalid) as refused:
            assemble(
                skeleton, gold, document(), ALL_PHASES, BundleDraftOutput.model_validate(dirty)
            )
        [error] = [e for e in refused.value.errors if "Détail identifiant" in e]
        assert f"({kind})" in error and "facts[0].patient_phrases_de[0]" in error
        # The excerpt is masked: the finding never carries what it found.
        assert sentence not in error and "Weber" not in error
    # A form of address is a title, not a name; a name after the title is one.
    for sentence, flagged in (
        ("Guten Tag, Herr Doktor. Ich habe Schmerzen.", False),
        ("Danke, Frau Doktor, das hilft mir.", False),
        ("Guten Tag, Herr Doktor Weber.", True),
        ("Mein Nachbar Herr Schmidt hat mich gebracht.", True),
    ):
        greeting = clean.model_dump()
        greeting["opening_de"] = sentence
        if flagged:
            with pytest.raises(BundleDraftInvalid):
                assemble(
                    skeleton,
                    gold,
                    document(),
                    ALL_PHASES,
                    BundleDraftOutput.model_validate(greeting),
                )
        else:
            assemble(
                skeleton, gold, document(), ALL_PHASES, BundleDraftOutput.model_validate(greeting)
            )
    title = clean.model_dump()
    title["title_de"] = "Fall von Herr Becker"
    with pytest.raises(BundleDraftInvalid) as in_title:
        assemble(skeleton, gold, document(), ALL_PHASES, BundleDraftOutput.model_validate(title))
    assert any("title_de" in error for error in in_title.value.errors)


def test_worker_stores_drafts_and_invalid_outputs_with_their_reasons(
    content_container: ContentContainer, tmp_path: Path
) -> None:
    pdf = synthetic_protocol_pdf(tmp_path / "one.pdf", count=1)
    result = content_container.ingestion.ingest(
        pdf.read_bytes(), pdf.name, declaration(), actor=OPERATOR, via="cli"
    )
    drain(content_container)
    with content_container.repository.transaction() as tx:
        head = tx.list_heads(document_id=result.document.id)[0]
    workflow = content_container.workflow
    doctor = Actor(kind="account", name="Doc", account_id="doc-1")
    owner = Actor(kind="account", name="Owner", account_id="owner-1")
    workflow.release(head.id, actor=owner)
    revised = workflow.revise(
        head.id,
        base_version=head.version,
        base_hash=head.content_hash,
        record=settle(head.record),
        actor=doctor,
        via="doctor",
    )
    workflow.doctor_decide(
        head.id,
        version=revised.version,
        protocol_hash=revised.content_hash,
        decision="approve",
        notes="",
        actor=doctor,
    )
    workflow.owner_decide(
        head.id,
        version=revised.version,
        protocol_hash=revised.content_hash,
        decision="approve",
        notes="",
        actor=owner,
    )

    request = BundleVariantRequest(phases=("arzt_patient", "arzt_arzt"), persona_variant="anxious")
    job = content_container.drafting.request(head.id, request, actor=owner)
    assert job.type.value == "generate_bundle_draft" and job.protocol_id == head.id
    with pytest.raises(NotFoundError):
        content_container.drafting.request("P-unknown-000", request, actor=owner)
    assert drain(content_container) == 1
    done = content_container.queue.get(job.id)
    assert done.status is JobStatus.SUCCEEDED, done.last_error
    with content_container.repository.transaction() as tx:
        drafts = tx.list_bundle_drafts(gold_protocol_id=head.id)
        runs = [run for run in tx.ai_runs() if run.operation == "bundle_draft"]
    assert len(drafts) == 1 and drafts[0].status is BundleDraftStatus.DRAFT
    draft = drafts[0]
    assert draft.case_id == f"FSP-BY-{head.id}" and draft.case_version == "1"
    assert [ref.phase for ref in draft.scenario_refs] == ["arzt_patient", "arzt_arzt"]
    assert draft.bundle is not None and draft.created_by_account_id == "owner-1"
    assert len(runs) == 1 and runs[0].id == draft.ai_run_id and runs[0].protocol_id == head.id
    stored = ClinicalBundle.model_validate_json(json.dumps(draft.bundle))  # a valid bundle
    # The requested temperament travels with the patient scenario (exam voice and wording).
    patient = next(s for s in stored.scenarios if s.phase == "arzt_patient")
    assert patient.persona_variant == "anxious"
    assert all(s.persona_variant is None for s in stored.scenarios if s.phase != "arzt_patient")

    # A model output that ignores the skeleton becomes an invalid draft, not a dead job.
    careless = build_content_container(
        content_container.settings,
        llm=FakeLLMProvider(
            handlers={
                **FAKE_CONTENT_HANDLERS,
                BundleDraftOutput: lambda payload: {**bundle_draft(payload), "facts": []},
            }
        ),
    )
    invalid_job = careless.drafting.request(
        head.id, request.model_copy(update={"revision": 2}), actor=owner
    )
    assert drain(careless) == 1
    finished = careless.queue.get(invalid_job.id)
    assert finished.status is JobStatus.SUCCEEDED, finished.last_error
    with careless.repository.transaction() as tx:
        newest = tx.list_bundle_drafts(gold_protocol_id=head.id)[0]
    careless.repository.engine.dispose()  # type: ignore[attr-defined]
    assert newest.status is BundleDraftStatus.INVALID and newest.bundle is None
    assert newest.validation_errors and all(
        "fait sans phrases patient" in error for error in newest.validation_errors
    )

    # A model that writes a real-looking name also yields an invalid draft: it never reaches
    # the registry, and its reason names the field without echoing the name.
    leaky = build_content_container(
        content_container.settings,
        llm=FakeLLMProvider(
            handlers={
                **FAKE_CONTENT_HANDLERS,
                BundleDraftOutput: lambda payload: {
                    **bundle_draft(payload),
                    "persona_de": "Patient Herr Weber, 54 Jahre",
                },
            }
        ),
    )
    leaky_job = leaky.drafting.request(
        head.id, request.model_copy(update={"revision": 3}), actor=owner
    )
    assert drain(leaky) == 1
    assert leaky.queue.get(leaky_job.id).status is JobStatus.SUCCEEDED
    with leaky.repository.transaction() as tx:
        newest = tx.list_bundle_drafts(gold_protocol_id=head.id)[0]
    leaky.repository.engine.dispose()  # type: ignore[attr-defined]
    assert newest.status is BundleDraftStatus.INVALID and newest.bundle is None
    assert newest.validation_errors == (
        "Détail identifiant dans le texte généré (person) : persona_de « He……… »",
    )
