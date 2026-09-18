"""Synthetic protocol records for the content pipeline tests. Fiction, never a real exam."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ari.content.domain.protocol import (
    AnamnesisItem,
    ArztArztSummary,
    Diagnosis,
    ExaminerQuestion,
    Fachbegriff,
    FieldUncertainty,
    GoldProtocol,
    Outcome,
    PatientPresentation,
    Pedagogy,
    Pitfall,
    ProtocolLocation,
    ProtocolQuestion,
    ProtocolRecord,
    ProtocolSource,
    PseudonymisationReport,
)
from ari.domain.geography import Land

DOCUMENT_ID = "c" * 64
FROZEN_AT = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def synthetic_record(**overrides: Any) -> ProtocolRecord:
    """An AI-style draft: one open question and one field uncertainty, no pedagogy yet."""
    base = ProtocolRecord(
        source=ProtocolSource(document_id=DOCUMENT_ID, segment_index=0, page_from=1, page_to=2),
        location=ProtocolLocation(
            land=Land.BAYERN, city=None, exam_body=None, exam_date="2026-03", specialty="Innere"
        ),
        patient=PatientPresentation(
            age_years=54, sex="female", presenting_complaint_de="Thoraxschmerz"
        ),
        anamnesis=(
            AnamnesisItem(
                id="a01",
                section="aktuelle_beschwerden",
                label_de="Thoraxschmerz",
                value_de="Drückender Schmerz seit zwei Stunden",
                polarity="present",
                source_pages=(1,),
            ),
            AnamnesisItem(
                id="a02",
                section="allergien",
                label_de="Allergien",
                value_de=None,
                polarity="unknown",
                uncertainty="Nicht im Protokoll erwähnt",
            ),
        ),
        diagnosis=Diagnosis(suspected_de="Akutes Koronarsyndrom"),
        examiner_questions=(
            ExaminerQuestion(
                id="q01", part="arzt_arzt", question_de="Stellen Sie die Patientin vor."
            ),
        ),
        arzt_arzt=ArztArztSummary(),
        fachbegriffe=(Fachbegriff(id="t01", german="Dyspnoe", lay_german="Atemnot"),),
        outcome=Outcome(result="unknown"),
        unresolved_questions=(
            ProtocolQuestion(id="u01", text_fr="Le résultat de l'examen est-il connu ?"),
        ),
        field_uncertainties=(
            FieldUncertainty(path="outcome.result", reason_fr="Non indiqué dans le protocole"),
        ),
        pseudonymisation=PseudonymisationReport(removed_kinds=("examiner_name",)),
        pedagogy=Pedagogy(),
    )
    return base.model_copy(update=overrides) if overrides else base


def settled_record() -> ProtocolRecord:
    """The same record after a doctor answered everything: no blocker at either stage."""
    draft = synthetic_record()
    allergies = draft.anamnesis[1].model_copy(
        update={"value_de": "Keine bekannt", "polarity": "absent", "uncertainty": None}
    )
    return ProtocolRecord.model_validate_json(
        draft.model_copy(
            update={
                "anamnesis": (draft.anamnesis[0], allergies),
                "unresolved_questions": (
                    draft.unresolved_questions[0].model_copy(update={"answer": "Bestanden"}),
                ),
                "field_uncertainties": (
                    draft.field_uncertainties[0].model_copy(update={"resolution": "Bestanden"}),
                ),
                "outcome": Outcome(result="passed"),
                "pedagogy": Pedagogy(
                    critical_pitfalls=(
                        Pitfall(text_fr="Oublier les allergies", related_item_ids=("a02",)),
                    ),
                    difficulty="mittel",
                ),
            }
        ).model_dump_json()
    )


def synthetic_gold(record: ProtocolRecord | None = None) -> GoldProtocol:
    record = record or settled_record()
    return GoldProtocol(
        protocol_id="P-cccccccc-000",
        protocol_version=2,
        protocol_hash=record.content_hash,
        location=record.location.as_case_location(),
        record=record,
        document_id=DOCUMENT_ID,
        rights="compatible",
        rights_evidence="Synthetic fixture",
        frozen_at=FROZEN_AT,
        frozen_by_account_id="owner-1",
    )
