"""Entirely synthetic contract examples, not human approvals."""

import hashlib
from datetime import UTC, datetime

from ari.domain.clinical import (
    CaseLocation,
    CaseReview,
    ClinicalAssessmentItem,
    ClinicalBundle,
    ClinicalCaseVersion,
    ClinicalFact,
    GoldProtocolRef,
    RawCaseSource,
    RubricDimension,
    RubricVersion,
    SourceRef,
    TerminologyEntry,
    TerminologySetVersion,
    TrainingScenarioVersion,
    VersionRef,
)
from ari.domain.geography import Land
from ari.domain.models import DisclosureRule, new_id

SYNTHETIC_PROTOCOL_HASH = hashlib.sha256(b"synthetic gold protocol, not a real exam").hexdigest()


def synthetic_gold_source(source_id: str = "synthetic-gold-protocol") -> RawCaseSource:
    return RawCaseSource(
        id=source_id,
        source_type="gold_protocol",
        synthetic=True,
        document_reference="isolated-test-fixture:gold-protocol",
        provenance="Invented protocol, not a real exam",
        imported_at=datetime(2026, 9, 4, tzinfo=UTC),
        rights="compatible",
        intended_use="isolated automated tests",
        rights_evidence="Synthetic test content authored for this repository",
        private_material_reference="none:synthetic",
    )


def synthetic_location(land: Land | None = Land.BAYERN) -> CaseLocation:
    return CaseLocation(land=land, city="Teststadt", exam_body=None, exam_date=None, specialty=None)


def synthetic_bundle(version: str = "1") -> ClinicalBundle:
    source = RawCaseSource(
        id="synthetic-source",
        source_type="synthetic",
        synthetic=True,
        document_reference="isolated-test-fixture",
        provenance="Invented fixture, not a clinical case",
        imported_at=datetime(2026, 9, 4, tzinfo=UTC),
        rights="compatible",
        intended_use="isolated automated tests",
        rights_evidence="Synthetic test content authored for this repository",
        private_material_reference="none:synthetic",
    )
    gold_source = synthetic_gold_source()
    rubric = RubricVersion(
        id="synthetic-rubric",
        version="1",
        dimensions=tuple(
            RubricDimension(id=id_, label=id_, max_score=5, description="Synthetic test criterion")
            for id_ in ("clinical_coverage", "communication", "structure", "language", "vocabulary")
        ),
    )
    terms = TerminologySetVersion(
        id="synthetic-terms",
        version="1",
        entries=(TerminologyEntry(id="symptom", german="Schmerz", french="douleur"),),
    )
    source_refs = (
        SourceRef(source_id=gold_source.id, pages=(1,)),
        SourceRef(source_id=source.id, pages=(1,)),
    )
    case = ClinicalCaseVersion(
        id="SYNTHETIC-TEST",
        version=version,
        gold_protocol=GoldProtocolRef(
            protocol_id="SYNTHETIC-PROTOCOL",
            protocol_version="1",
            protocol_hash=SYNTHETIC_PROTOCOL_HASH,
        ),
        protocol_source_id=gold_source.id,
        location=synthetic_location(),
        title="SYNTHETIC — kein realer Fall",
        public_summary="Offline test only",
        transcription_context="Synthetischer Test",
        unknown_response="Das weiß ich nicht.",
        out_of_scope_response="Dazu kann ich nichts sagen.",
        sources=source_refs,
        facts=tuple(
            ClinicalFact(
                id=f"fact-{i}",
                category="test",
                value=i,
                unit="days",
                temporality="current",
                polarity="present",
                patient_phrases_de=(f"Seit {i} Tagen.",),
                translation_fr=f"Depuis {i} jours.",
                disclosure=DisclosureRule.WHEN_ASKED,
                sources=source_refs,
            )
            for i in (1, 2)
        ),
        assessment_items=(
            ClinicalAssessmentItem(
                id="clinical-all",
                dimension="clinical_coverage",
                weight=3.0,
                required=True,
                expected_behavior="Hear both test facts",
                evidence_kind="delivered_facts",
                satisfaction="all",
                satisfied_by_fact_ids=("fact-1", "fact-2"),
            ),
            ClinicalAssessmentItem(
                id="clinical-any",
                dimension="clinical_coverage",
                weight=1.0,
                expected_behavior="Hear a test fact",
                evidence_kind="delivered_facts",
                satisfaction="any",
                satisfied_by_fact_ids=("fact-1", "fact-2"),
            ),
            *(
                ClinicalAssessmentItem(
                    id=d,
                    dimension=d,
                    expected_behavior="Ask permission",
                    evidence_kind="doctor_quote",
                    satisfaction="any",
                    doctor_phrases=("Darf ich",),
                )
                for d in ("communication", "structure", "language", "vocabulary")
            ),
        ),
    )
    scenario = TrainingScenarioVersion(
        id="synthetic-scenario",
        version=version,
        case=VersionRef(id=case.id, version=case.version),
        case_hash=case.content_hash,
        rubric=VersionRef(id=rubric.id, version=rubric.version),
        rubric_hash=rubric.content_hash,
        terminology=VersionRef(id=terms.id, version=terms.version),
        terminology_hash=terms.content_hash,
        phase="arzt_patient",
        persona="Synthetische Testperson",
        difficulty="test",
        cefr="C1",
        opening="Guten Tag.",
        objectives=("Offline integration test",),
    )
    return ClinicalBundle(
        sources=(gold_source, source),
        rubrics=(rubric,),
        terminology_sets=(terms,),
        cases=(case,),
        scenarios=(scenario,),
    )


def simulated_review(bundle: ClinicalBundle, kind: str) -> CaseReview:
    return CaseReview.model_validate_json(
        __import__("json").dumps(
            {
                "id": new_id(),
                "case": bundle.scenarios[0].case.model_dump(),
                "case_hash": bundle.cases[0].content_hash,
                "scenario": {"id": bundle.scenarios[0].id, "version": bundle.scenarios[0].version},
                "scenario_hash": bundle.scenarios[0].content_hash,
                "review_type": kind,
                "reviewer_name": "SIMULATED TEST REVIEWER — NOT A HUMAN APPROVAL",
                "reviewed_at": "2026-09-04T10:00:00Z",
                "decision": "approve",
                "notes": "Isolated test only",
            }
        )
    )
