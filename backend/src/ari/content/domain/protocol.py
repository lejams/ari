"""Protocol records: the ground truth extracted from exam protocols, before any training case.

A `ProtocolRecord` is what the AI drafts from one protocol, what the doctor corrects and what
the owner freezes as a `GoldProtocol`. Everything the AI could not establish is carried as an
explicit uncertainty or question, never filled in. Same strict, frozen, hashed contract as the
clinical authoring models.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ari.domain.clinical import (
    AnamnesisSectionId,
    CaseLocation,
    ClinicalModel,
    Digest,
    Identifier,
    Text,
    YearMonth,
    unique,
)
from ari.domain.clinical_privacy import contact_locations
from ari.domain.geography import Land

PROTOCOL_SCHEMA_VERSION: Literal["fsp-protocol-v1"] = "fsp-protocol-v1"
Page = Annotated[int, Field(ge=1)]
Polarity = Literal["present", "absent", "unknown"]
ReviewStage = Literal["doctor", "owner"]
Decision = Literal["approve", "request_changes", "reject"]


class ProtocolStatus(StrEnum):
    EXTRACTED = "extracted"
    DOCTOR_REVIEW = "doctor_review"
    CHANGES_REQUESTED = "changes_requested"
    DOCTOR_APPROVED = "doctor_approved"
    GOLD = "gold"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"  # An older version replaced by a correction.


class ProtocolLocation(ClinicalModel):
    """Draft form of `CaseLocation`: `land` may still be unknown before the gold decision."""

    land: Land | None
    city: Text | None
    exam_body: Text | None
    exam_date: YearMonth | None
    specialty: Text | None

    def as_case_location(self) -> CaseLocation:
        return CaseLocation(
            land=self.land,
            city=self.city,
            exam_body=self.exam_body,
            exam_date=self.exam_date,
            specialty=self.specialty,
        )


class ProtocolSource(ClinicalModel):
    """Where the protocol comes from: the document and the page span the segmentation found."""

    document_id: Digest
    segment_index: Annotated[int, Field(ge=0)]
    page_from: Page
    page_to: Page
    title_hint: Text | None = None
    date_hint: Text | None = None
    land_hint: Text | None = None  # Hints stay hints: never copied into `location`.
    city_hint: Text | None = None

    @model_validator(mode="after")
    def pages_in_order(self) -> Self:
        if self.page_to < self.page_from:
            raise ValueError("La dernière page précède la première")
        return self


class PatientPresentation(ClinicalModel):
    age_years: Annotated[int, Field(ge=0, le=120)] | None
    sex: Literal["female", "male", "other", "unknown"]
    occupation_de: Text | None = None
    presenting_complaint_de: Text | None = None
    summary_de: Text | None = None
    uncertainty: Text | None = None


class AnamnesisItem(ClinicalModel):
    """One statement of the anamnesis, filed under a canonical FSP section."""

    id: Identifier
    section: AnamnesisSectionId
    label_de: Text
    value_de: Text | None
    polarity: Polarity
    temporality: Text | None = None
    quote_de: Text | None = None  # Short verbatim fragment, already pseudonymised.
    source_pages: tuple[Page, ...] = ()
    uncertainty: Text | None = None

    @model_validator(mode="after")
    def value_matches_polarity(self) -> Self:
        if self.polarity == "unknown" and self.value_de is not None:
            raise ValueError("Une valeur inconnue doit être null, jamais une négation implicite")
        if self.polarity != "unknown" and self.value_de is None:
            raise ValueError("Une valeur manquante exige polarity: unknown")
        return self


class Diagnosis(ClinicalModel):
    suspected_de: Text | None = None
    differentials_de: tuple[Text, ...] = ()
    uncertainty: Text | None = None


QuestionPart = Literal["arzt_patient", "arzt_arzt", "fachbegriffe", "arztbrief", "general"]


class ExaminerQuestion(ClinicalModel):
    id: Identifier
    part: QuestionPart
    question_de: Text
    expected_answer_de: Text | None = None
    candidate_answer_de: Text | None = None
    source_pages: tuple[Page, ...] = ()
    uncertainty: Text | None = None


class ArztArztSummary(ClinicalModel):
    presentation_summary_de: Text | None = None
    discussion_points_de: tuple[Text, ...] = ()
    uncertainty: Text | None = None


class Fachbegriff(ClinicalModel):
    id: Identifier
    german: Text
    lay_german: Text | None = None
    french: Text | None = None
    asked: bool = True  # Explicitly asked by the examiners, or merely used in the protocol.


class Outcome(ClinicalModel):
    result: Literal["passed", "failed", "unknown"]
    examiner_feedback_de: tuple[Text, ...] = ()
    candidate_tips_de: tuple[Text, ...] = ()
    uncertainty: Text | None = None


class ProtocolQuestion(ClinicalModel):
    """A question the AI asks the reviewing doctor. Critical questions block approval."""

    id: Identifier
    text_fr: Text
    critical: bool = True
    answer: Text | None = None


UncertaintyPath = Literal[
    "location.land",
    "location.city",
    "location.exam_body",
    "location.exam_date",
    "location.specialty",
    "patient.age_years",
    "patient.sex",
    "diagnosis.suspected_de",
    "outcome.result",
    "arzt_arzt",
]


class FieldUncertainty(ClinicalModel):
    """A doubt on a top-level field; item-level doubts live on the items themselves."""

    path: UncertaintyPath
    reason_fr: Text
    resolution: Text | None = None


RemovedKind = Literal[
    "patient_name",
    "examiner_name",
    "candidate_name",
    "hospital_name",
    "exact_date",
    "address",
    "contact",
    "other",
]


class PseudonymisationReport(ClinicalModel):
    removed_kinds: tuple[RemovedKind, ...] = ()
    notes: tuple[Text, ...] = ()


class Pitfall(ClinicalModel):
    """A mistake the doctor flags; linked items become critical facts in derived cases."""

    text_fr: Text
    related_item_ids: tuple[Identifier, ...] = ()


Difficulty = Literal["leicht", "mittel", "schwer"]


class Pedagogy(ClinicalModel):
    """Filled by the reviewing doctor, empty in the AI draft."""

    critical_pitfalls: tuple[Pitfall, ...] = ()
    pitfalls: tuple[Pitfall, ...] = ()
    difficulty: Difficulty | None = None
    notes_fr: Text | None = None


class ProtocolRecord(ClinicalModel):
    schema_version: Literal["fsp-protocol-v1"] = PROTOCOL_SCHEMA_VERSION
    # Real protocols are German; French exists for development fixtures only.
    language: Literal["de-DE", "fr-FR"] = "de-DE"
    source: ProtocolSource
    location: ProtocolLocation
    patient: PatientPresentation
    anamnesis: tuple[AnamnesisItem, ...] = ()
    diagnosis: Diagnosis
    examiner_questions: tuple[ExaminerQuestion, ...] = ()
    arzt_arzt: ArztArztSummary
    fachbegriffe: tuple[Fachbegriff, ...] = ()
    arztbrief_notes_de: Text | None = None
    outcome: Outcome
    unresolved_questions: tuple[ProtocolQuestion, ...] = ()
    field_uncertainties: tuple[FieldUncertainty, ...] = ()
    pseudonymisation: PseudonymisationReport
    pedagogy: Pedagogy

    @model_validator(mode="after")
    def consistent(self) -> Self:
        item_ids = tuple(item.id for item in self.anamnesis)
        question_ids = tuple(question.id for question in self.examiner_questions)
        term_ids = tuple(term.id for term in self.fachbegriffe)
        unique(item_ids, "items d'anamnèse")
        unique(question_ids, "questions d'examinateur")
        unique(term_ids, "Fachbegriffe")
        unique(tuple(question.id for question in self.unresolved_questions), "questions ouvertes")
        unique(tuple(u.path for u in self.field_uncertainties), "incertitudes de champ")
        known = set(item_ids) | set(question_ids) | set(term_ids)
        for pitfall in (*self.pedagogy.critical_pitfalls, *self.pedagogy.pitfalls):
            if set(pitfall.related_item_ids) - known:
                raise ValueError("Un piège référence un élément inconnu du protocole")
        if contact_locations(self.model_dump(mode="json")):
            raise ValueError("Coordonnées personnelles détectées dans le protocole")
        return self

    @property
    def open_uncertainties(self) -> tuple[str, ...]:
        """Every doubt still standing, as `path: reason`; empty means the record is settled."""
        found: list[str] = []
        for holder, name in (
            (self.patient, "patient"),
            (self.diagnosis, "diagnosis"),
            (self.arzt_arzt, "arzt_arzt"),
            (self.outcome, "outcome"),
        ):
            if holder.uncertainty:
                found.append(f"{name}: {holder.uncertainty}")
        found.extend(f"anamnesis.{i.id}: {i.uncertainty}" for i in self.anamnesis if i.uncertainty)
        found.extend(
            f"examiner_questions.{q.id}: {q.uncertainty}"
            for q in self.examiner_questions
            if q.uncertainty
        )
        found.extend(
            f"{u.path}: {u.reason_fr}" for u in self.field_uncertainties if u.resolution is None
        )
        return tuple(found)

    @property
    def open_critical_questions(self) -> tuple[ProtocolQuestion, ...]:
        return tuple(q for q in self.unresolved_questions if q.critical and q.answer is None)

    def review_blockers(self, stage: ReviewStage) -> tuple[str, ...]:
        """What still prevents an approval at this stage. Deterministic, shown as a checklist."""
        blockers = [f"Incertitude ouverte: {text}" for text in self.open_uncertainties]
        blockers += [
            f"Question critique sans réponse: {q.id}" for q in self.open_critical_questions
        ]
        if self.pedagogy.difficulty is None:
            blockers.append("Difficulté non renseignée")
        if stage == "owner" and self.location.land is None:
            blockers.append("Land manquant")
        return tuple(blockers)


class ProtocolReview(ClinicalModel):
    id: Identifier
    protocol_id: Identifier
    protocol_version: Annotated[int, Field(ge=1)]
    protocol_hash: Digest
    stage: ReviewStage
    decision: Decision
    reviewer_account_id: Identifier
    reviewer_name: Text
    reviewed_at: datetime
    notes: str = ""  # Empty is fine for an approval; a refusal should say why.
    pii_override_note: Text | None = None

    @model_validator(mode="after")
    def timezone_required(self) -> Self:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("La date de revue doit avoir un fuseau horaire")
        return self


class GoldProtocol(ClinicalModel):
    """The frozen record: owner-validated, settled, located. Its hash is the `gold_hash`."""

    protocol_id: Identifier
    protocol_version: Annotated[int, Field(ge=1)]
    protocol_hash: Digest
    location: CaseLocation
    record: ProtocolRecord
    document_id: Digest
    rights: Literal["unknown", "incompatible", "compatible"]
    rights_evidence: Text | None
    frozen_at: datetime
    frozen_by_account_id: Identifier | None

    @model_validator(mode="after")
    def settled(self) -> Self:
        if self.record.content_hash != self.protocol_hash:
            raise ValueError("Le hash du protocole ne correspond pas au contenu figé")
        if self.location.land is None or self.location != self.record.location.as_case_location():
            raise ValueError("La localisation gold doit être complète et identique au protocole")
        if self.record.review_blockers("owner"):
            raise ValueError("Un protocole gold ne garde aucune incertitude ni question ouverte")
        if self.rights == "incompatible":
            raise ValueError("Des droits incompatibles ne peuvent pas devenir gold")
        if self.frozen_at.tzinfo is None:
            raise ValueError("La date de gel doit avoir un fuseau horaire")
        return self
