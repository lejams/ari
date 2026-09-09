"""Strict transport schema for the received Work 0.1 drafts, not executable cases.

Source assertions remain unvalidated. Nullable source fields have no invented defaults.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ari.domain.clinical import ClinicalModel, Digest, Identifier, Text, unique

Page = Annotated[int, Field(gt=0)]
Pages = Annotated[tuple[Page, ...], Field(min_length=1)]
Number = Annotated[int | float, Field(ge=0)]


class WorkSource(ClinicalModel):
    source_id: Identifier
    document: Text
    url: Text
    pages: Text
    document_date: Text | None
    license: Text
    checksum: Digest | None
    checksum_status: Text
    role: Text
    privacy: Text | None
    needs_review: Literal[True]


class WorkValidation(ClinicalModel):
    medical: Literal[False]
    linguistic: Literal[False]
    human_review_required: Literal[True]


class ReportedDiagnosis(ClinicalModel):
    de: Text
    fr: Text
    medically_validated: Literal[False]
    qualifier: Literal["reported_in_community_source"]


class Demographics(ClinicalModel):
    name: Text | None
    age_years: Number | None
    sex: Literal["male", "female", "unknown"]
    height_cm: Number | None
    weight_kg: Number | None
    occupation_de: Text | None
    occupation_fr: Text | None
    marital_status_de: Text | None
    marital_status_fr: Text | None
    children: Annotated[int, Field(ge=0)] | None
    needs_review: Literal[True]


class Medication(ClinicalModel):
    name: Text
    dose: Number | None
    unit: Text | None
    schedule: Text | None
    indication_reported: Text | None
    dose_unknown: bool

    @model_validator(mode="after")
    def consistent_dose(self) -> Self:
        if self.dose_unknown != (self.dose is None):
            raise ValueError("Dose et marqueur dose_unknown contradictoires")
        if self.dose is not None and self.unit is None:
            raise ValueError("Dose chiffrée sans unité")
        return self


class WorkFact(ClinicalModel):
    fact_id: Identifier
    category: Text
    concept: Text
    polarity: Literal["present", "absent", "unknown"]
    value_de: Text | None
    value_fr: Text | None
    value_type: Literal["text"]
    numeric_value: int | float | None
    unit: Literal["pack-years", "cigarettes/day", "L", "kg", "°C", "episodes", "floors"] | None
    temporal_de: Text | None
    temporal_fr: Text | None
    medication: Medication | None
    patient_phrase_de: Text
    founder_translation_fr: Text | None
    disclosure_rule: Literal[
        "opening_then_details_if_asked",
        "spontaneous_with_chief_complaint",
        "answer_if_asked",
        "spontaneous_after_symptoms",
        "if_examiner_asks_local_signs",
        "opening",
        "spontaneous_after_family_history",
    ]
    source_pages: Pages
    needs_review: Literal[True]

    @model_validator(mode="after")
    def consistent_value(self) -> Self:
        if (self.numeric_value is None) != (self.unit is None):
            raise ValueError("Nombre et unité doivent être fournis ensemble")
        if self.polarity == "unknown" and self.numeric_value is not None:
            raise ValueError("Une valeur inconnue ne peut pas être un nombre certain")
        if self.polarity != "unknown" and self.value_de is None:
            raise ValueError("Une valeur manquante ne signifie pas présence ou absence")
        return self


class WorkAssessment(ClinicalModel):
    assessment_item_id: Identifier
    type: Literal["information_gathering", "arzt_arzt_question"]
    prompt_de: Text
    prompt_fr: Text | None
    expected_answer_de: Text | None = None
    expected_answer_fr: Text | None = None
    weight: Annotated[int | float, Field(gt=0)]
    required: bool
    satisfied_by_fact_ids: tuple[Identifier, ...]
    source_pages: Pages
    needs_review: Literal[True]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        unique(self.satisfied_by_fact_ids, "références de faits")
        if self.type == "information_gathering" and not self.satisfied_by_fact_ids:
            raise ValueError("Recueil d'information sans référence de fait")
        if self.type == "arzt_arzt_question" and self.satisfied_by_fact_ids:
            raise ValueError("Une question médecin-médecin n'est pas un fait livré")
        return self


class WorkTerm(ClinicalModel):
    term_de: Text
    translation_fr: Text | None
    source_pages: Pages
    case_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    note: Text | None
    needs_review: Literal[True]


class DoctorQuestion(ClinicalModel):
    de: Text
    fr: Text | None
    pages: Pages
    needs_review: Literal[True]


class WorkQuestion(ClinicalModel):
    question_id: Identifier
    case_id: Identifier | None
    category: Text
    question_fr: Text
    source_pages: tuple[Page, ...]
    needs_review: Literal[True]


class WorkCase(ClinicalModel):
    case_id: Identifier
    version: Identifier
    status: Literal["draft_unvalidated"]
    title_de: Text
    title_fr: Text
    language: Literal["de-DE"]
    region: Text
    city: Text
    fsp_phase: Literal["arzt_patient"]
    source_document_id: Identifier
    source_pages: Pages
    source_exam_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    reported_diagnosis: ReportedDiagnosis
    demographics: Demographics
    opening_line_de: Text | None
    opening_line_fr: Text | None
    facts: Annotated[tuple[WorkFact, ...], Field(min_length=1)]
    assessment_items: Annotated[tuple[WorkAssessment, ...], Field(min_length=1)]
    terminology: tuple[WorkTerm, ...]
    arzt_arzt_questions: tuple[DoctorQuestion, ...]
    open_questions: tuple[Text, ...]
    publication_note_fr: Text
    needs_review: Literal[True]

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        ids = tuple(f.fact_id for f in self.facts)
        unique(ids, "faits")
        unique(tuple(a.assessment_item_id for a in self.assessment_items), "critères")
        for pages in (
            *(f.source_pages for f in self.facts),
            *(a.source_pages for a in self.assessment_items),
            *(t.source_pages for t in self.terminology),
        ):
            if not set(pages) <= set(self.source_pages):
                raise ValueError("Page hors des références du cas")
        for item in self.assessment_items:
            if not set(item.satisfied_by_fact_ids) <= set(ids):
                raise ValueError("Référence de fait inconnue ou d'un autre cas")
        questions = [a for a in self.assessment_items if a.type == "arzt_arzt_question"]
        if [(a.prompt_de, a.prompt_fr, a.source_pages) for a in questions] != [
            (q.de, q.fr, q.pages) for q in self.arzt_arzt_questions
        ]:
            raise ValueError("Questions médecin-médecin et critères incohérents")
        return self


class FreiburgExport(ClinicalModel):
    schema_version: Literal["ari-clinical-cases-draft-0.1"]
    generated_on: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    status: Literal["draft_unvalidated"]
    validation: WorkValidation
    sources: Annotated[tuple[WorkSource, ...], Field(min_length=1)]
    cases: Annotated[tuple[WorkCase, ...], Field(min_length=1)]
    terminology: tuple[WorkTerm, ...]
    open_questions: tuple[WorkQuestion, ...]
    privacy_note: Text

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        ids = tuple(c.case_id for c in self.cases)
        sources = tuple(s.source_id for s in self.sources)
        unique(ids, "cas")
        unique(sources, "sources")
        unique(tuple(q.question_id for q in self.open_questions), "questions")
        for term in self.terminology:
            unique(term.case_ids, "références de cas")
            if not set(term.case_ids) <= set(ids):
                raise ValueError("Terme référençant un cas inconnu")
        for question in self.open_questions:
            if question.case_id is not None and question.case_id not in ids:
                raise ValueError("Question référençant un cas inconnu")
        for case in self.cases:
            if case.source_document_id not in sources:
                raise ValueError("Source clinique inconnue")
            for question in self.open_questions:
                if question.case_id == case.case_id and not set(question.source_pages) <= set(
                    case.source_pages
                ):
                    raise ValueError("Question liée au cas avec une page hors cas")
            if case.terminology != tuple(t for t in self.terminology if case.case_id in t.case_ids):
                raise ValueError("Lexique partagé et lexique du cas incohérents")
            if case.open_questions != tuple(
                q.question_fr for q in self.open_questions if q.case_id == case.case_id
            ):
                raise ValueError("Questions globales et questions du cas incohérentes")
        return self
