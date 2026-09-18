"""Versioned clinical authoring contract. No provider or persistence dependencies."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from ari.domain.clinical_privacy import contact_locations
from ari.domain.geography import Land
from ari.domain.models import DisclosureRule

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
YearMonth = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


class ClinicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class VersionRef(ClinicalModel):
    id: Identifier
    version: Identifier


class SourceRef(ClinicalModel):
    source_id: Identifier
    pages: tuple[Annotated[int, Field(gt=0)], ...] = ()


class RawCaseSource(ClinicalModel):
    id: Identifier
    source_type: Literal["work_export", "original_document", "synthetic", "gold_protocol"]
    # Fiction written for tests and development. A synthetic gold protocol keeps the
    # traceability of a real one while staying out of any learner-facing claim.
    synthetic: bool = False
    document_reference: Text
    original_checksum: Digest | None = None
    immediate_source_checksum: Digest | None = None
    provenance: Text
    imported_at: datetime
    rights: Literal["unknown", "incompatible", "compatible"] = "unknown"
    intended_use: Text
    rights_evidence: Text | None = None
    private_material_reference: Text
    declared_original: Text | None = None
    original_verified: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.imported_at.tzinfo is None:
            raise ValueError("La date d'import doit avoir un fuseau horaire")
        if self.rights == "compatible" and not self.rights_evidence:
            raise ValueError("Les droits compatibles exigent une justification explicite")
        if self.source_type == "work_export" and self.original_verified:
            raise ValueError("Un export Work ne prouve pas la vérification du PDF")
        if self.source_type == "synthetic" and not self.synthetic:
            raise ValueError("Une source de type synthetic doit être marquée synthetic: true")
        return self


class GoldProtocolRef(ClinicalModel):
    """The validated exam protocol a case is derived from; every clinical fact traces to it."""

    protocol_id: Identifier
    protocol_version: Identifier
    protocol_hash: Digest


class CaseLocation(ClinicalModel):
    """Where the protocol was examined. Every key is explicit: absent means null, never guessed."""

    land: Land | None
    city: Text | None
    exam_body: Text | None  # The examining Ärztekammer, when the protocol names it.
    exam_date: YearMonth | None  # Month precision on purpose: an exact day helps re-identification.
    specialty: Text | None

    @field_validator("city", mode="before")
    @classmethod
    def normalise_city(cls, value: object) -> object:
        return " ".join(value.split()) if isinstance(value, str) else value


class UnresolvedQuestion(ClinicalModel):
    id: Identifier
    text: Text
    critical: bool = True


class ClinicalFact(ClinicalModel):
    id: Identifier
    category: Text
    value: str | int | float | bool | None
    unit: (
        Literal[
            "mg",
            "g",
            "kg",
            "mL",
            "L",
            "mmHg",
            "bpm",
            "°C",
            "mmol/L",
            "mg/dL",
            "cm",
            "mm",
            "years",
            "days",
            "hours",
            "%",
        ]
        | None
    ) = None
    temporality: Text | None = None
    polarity: Literal["present", "absent", "unknown"]
    criticality: Literal["normal", "critical"] = "normal"
    patient_phrases_de: tuple[Text, ...] = Field(min_length=1)
    translation_fr: Text | None = None
    disclosure: DisclosureRule
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    uncertainty: Text | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if self.polarity == "unknown" and self.value is not None:
            raise ValueError("Une valeur inconnue doit être null, jamais une négation implicite")
        if self.polarity != "unknown" and self.value is None:
            raise ValueError("Une valeur manquante exige polarity: unknown")
        if self.unit is not None and (type(self.value) not in (int, float)):
            raise ValueError("Une unité exige une valeur numérique explicite")
        return self


class ClinicalAssessmentItem(ClinicalModel):
    id: Identifier
    dimension: Identifier
    weight: Annotated[float, Field(gt=0)] = 1.0
    required: bool = False
    expected_behavior: Text
    evidence_kind: Literal["delivered_facts", "doctor_quote"]
    satisfaction: Literal["all", "any"]
    satisfied_by_fact_ids: tuple[Identifier, ...] = ()
    # Exact, human-authored phrases; case-insensitive transcript matching, not an LLM judgment.
    doctor_phrases: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        unique(self.satisfied_by_fact_ids, "références de faits")
        unique(self.doctor_phrases, "phrases de preuve")
        if self.evidence_kind == "delivered_facts":
            if not self.satisfied_by_fact_ids or self.doctor_phrases:
                raise ValueError(
                    "Règle de faits: faits requis, phrases comportementales interdites"
                )
        elif not self.doctor_phrases:
            raise ValueError("Un comportement exige des phrases de preuve dans le transcript")
        return self


class RubricDimension(ClinicalModel):
    id: Identifier
    label: Text
    max_score: Annotated[int, Field(ge=1, le=5)]
    description: Text


class RubricVersion(VersionRef):
    dimensions: tuple[RubricDimension, ...] = Field(min_length=1)
    scoring_version: Literal[
        "assessment-weighted-v1",
        "practice-exact-answer-v1",
    ] = "assessment-weighted-v1"

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        unique(tuple(item.id for item in self.dimensions), "dimensions")
        return self


class TerminologyEntry(ClinicalModel):
    id: Identifier
    german: Text
    french: Text | None = None


class TerminologySetVersion(VersionRef):
    entries: tuple[TerminologyEntry, ...]

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        unique(tuple(item.id for item in self.entries), "termes")
        return self


class ClinicalCaseVersion(VersionRef):
    schema_version: Literal["clinical-case-v3"] = "clinical-case-v3"
    # Published learner content is German. Other languages exist for development bundles only.
    language: Literal["de-DE", "fr-FR", "en-US"] = "de-DE"
    gold_protocol: GoldProtocolRef
    protocol_source_id: Identifier  # The `gold_protocol` source every fact must cite.
    location: CaseLocation  # Copied from the gold protocol; lets the catalogue filter by Land.
    title: Text
    public_summary: Text
    transcription_context: Text
    unknown_response: Text
    out_of_scope_response: Text
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    unresolved_questions: tuple[UnresolvedQuestion, ...] = ()
    facts: tuple[ClinicalFact, ...] = Field(min_length=1)
    assessment_items: tuple[ClinicalAssessmentItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_refs(self) -> Self:
        unique(tuple(item.id for item in self.facts), "faits")
        unique(tuple(item.id for item in self.assessment_items), "items")
        unique(tuple(item.id for item in self.unresolved_questions), "questions")
        unique(tuple(item.source_id for item in self.sources), "sources")
        fact_ids = {item.id for item in self.facts}
        source_ids = {item.source_id for item in self.sources}
        if self.protocol_source_id not in source_ids:
            raise ValueError("La source du protocole gold doit figurer dans les sources du cas")
        for fact in self.facts:
            if {ref.source_id for ref in fact.sources} - source_ids:
                raise ValueError(f"Source inconnue pour le fait {fact.id}")
            if all(ref.source_id != self.protocol_source_id for ref in fact.sources):
                raise ValueError(f"Le fait {fact.id} ne trace pas vers le protocole gold")
        for item in self.assessment_items:
            if set(item.satisfied_by_fact_ids) - fact_ids:
                raise ValueError(f"Fait inconnu pour l'item {item.id}")
        return self

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(
            [f"Question critique {q.id}: {q.text}" for q in self.unresolved_questions if q.critical]
            + [
                f"Fait critique incomplet/incertain: {f.id}"
                for f in self.facts
                if f.criticality == "critical" and (f.polarity == "unknown" or f.uncertainty)
            ]
            + (["Land manquant"] if self.location.land is None else [])
        )


class PracticeQuestion(ClinicalModel):
    id: Identifier
    kind: Literal["presentation", "followup", "term_definition"]
    prompt_de: Text
    coaching_fr: Text
    assessment_item_id: Identifier
    term_id: Identifier | None = None


class PracticeCriterion(ClinicalModel):
    id: Identifier
    dimension: Identifier
    weight: Annotated[float, Field(gt=0)]
    expected_behavior: Text
    accepted_answers: tuple[Text, ...] = Field(min_length=1)
    evidence_rule: Literal["normalized_exact_answer"] = "normalized_exact_answer"


class PracticeSpecification(ClinicalModel):
    """Phase-specific criteria/questions, reviewed through the enclosing scenario hash.

    Case-wide patient criteria and clinical facts are not changed or repurposed.
    """

    schema_version: Literal["practice-spec-v1"] = "practice-spec-v1"
    scoring_version: Literal["practice-exact-answer-v1"] = "practice-exact-answer-v1"
    context_fact_ids: tuple[Identifier, ...] = ()
    questions: tuple[PracticeQuestion, ...] = Field(min_length=1, max_length=50)
    assessment_items: tuple[PracticeCriterion, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        unique(self.context_fact_ids, "faits de contexte")
        unique(tuple(q.id for q in self.questions), "questions d'exercice")
        unique(tuple(q.assessment_item_id for q in self.questions), "critères d'exercice")
        unique(tuple(i.id for i in self.assessment_items), "items d'exercice")
        if {q.assessment_item_id for q in self.questions} != {i.id for i in self.assessment_items}:
            raise ValueError("Chaque critère d'exercice doit avoir une question explicite")
        return self


# Canonical FSP anamnesis sections, in the order examiners expect them.
ANAMNESIS_SECTION_IDS = (
    "patientendaten",
    "aktuelle_beschwerden",
    "vorerkrankungen",
    "medikamente",
    "allergien",
    "noxen",
    "familienanamnese",
    "sozialanamnese",
    "vegetative_anamnese",
    "sonstiges",
)
AnamnesisSectionId = Literal[
    "patientendaten",
    "aktuelle_beschwerden",
    "vorerkrankungen",
    "medikamente",
    "allergien",
    "noxen",
    "familienanamnese",
    "sozialanamnese",
    "vegetative_anamnese",
    "sonstiges",
]


class EmpathyMoment(ClinicalModel):
    """A disclosure the doctor should acknowledge before moving on (pedagogical layer).

    The trigger is deterministic (the fact is delivered); only the judgement of the
    learner's next turn is delegated to the evaluator, with the turn as evidence.
    """

    id: Identifier
    fact_id: Identifier
    cue_fr: Text  # What the patient reveals, for the reviewer and the feedback.
    expected_fr: Text  # The expected reaction, e.g. acknowledge, pause, then return to the case.


class AnamnesisSection(ClinicalModel):
    id: AnamnesisSectionId
    label_de: Text
    fact_ids: tuple[Identifier, ...] = Field(min_length=1)


class TrainingScenarioVersion(VersionRef):
    case: VersionRef
    case_hash: Digest
    rubric: VersionRef
    rubric_hash: Digest
    terminology: VersionRef
    terminology_hash: Digest
    phase: Literal["arzt_patient", "arzt_arzt", "fachbegriffe", "arztbrief"]
    persona: Text
    difficulty: Text
    cefr: Literal["A1", "A2", "B1", "B2", "C1", "C2"]
    opening: Text
    opening_fact_ids: tuple[Identifier, ...] = ()
    objectives: tuple[Text, ...] = Field(min_length=1)
    duration_minutes: Annotated[int, Field(ge=1, le=240)] = 20
    practice: PracticeSpecification | None = None
    empathy_moments: tuple[EmpathyMoment, ...] = ()
    anamnesis_sections: tuple[AnamnesisSection, ...] = ()

    @model_validator(mode="after")
    def validate_pedagogy(self) -> Self:
        unique(tuple(m.id for m in self.empathy_moments), "moments d'empathie")
        unique(tuple(m.fact_id for m in self.empathy_moments), "faits des moments d'empathie")
        unique(tuple(s.id for s in self.anamnesis_sections), "sections d'anamnèse")
        unique(
            tuple(f for s in self.anamnesis_sections for f in s.fact_ids),
            "faits des sections d'anamnèse",
        )
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_serialization(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        payload: dict[str, Any] = handler(self)
        # Additive optional assets: old payloads/hashes/reviews remain byte-for-byte canonical.
        if self.practice is None:
            payload.pop("practice", None)
        if not self.empathy_moments:
            payload.pop("empathy_moments", None)
        if not self.anamnesis_sections:
            payload.pop("anamnesis_sections", None)
        return payload


class CaseReview(ClinicalModel):
    id: Identifier
    case: VersionRef
    case_hash: Digest
    scenario: VersionRef
    scenario_hash: Digest
    review_type: Literal["clinical", "linguistic"]
    reviewer_name: Text
    reviewed_at: datetime
    decision: Literal["approve", "request_changes", "reject"]
    notes: Text

    @model_validator(mode="after")
    def timezone_required(self) -> Self:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("La date de revue doit avoir un fuseau horaire")
        return self


def unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Identifiants dupliqués: {label}")


class ClinicalBundle(ClinicalModel):
    schema_version: Literal["ari-clinical-bundle-v1"] = "ari-clinical-bundle-v1"
    sources: tuple[RawCaseSource, ...]
    rubrics: tuple[RubricVersion, ...]
    terminology_sets: tuple[TerminologySetVersion, ...]
    cases: tuple[ClinicalCaseVersion, ...]
    scenarios: tuple[TrainingScenarioVersion, ...]

    @model_validator(mode="after")
    def references(self) -> Self:
        for section in (self.cases, self.scenarios, self.rubrics, self.terminology_sets):
            if contact_locations([model.model_dump(mode="json") for model in section]):
                raise ValueError(
                    "Coordonnées personnelles détectées dans le contenu d'entraînement"
                )
        unique(tuple(s.id for s in self.sources), "sources")
        for label, items in (
            ("rubriques", self.rubrics),
            ("lexiques", self.terminology_sets),
            ("cas", self.cases),
            ("scénarios", self.scenarios),
        ):
            unique(tuple(f"{i.id}@{i.version}" for i in items), label)
        sources = {s.id: s for s in self.sources}
        cases = {(c.id, c.version): c for c in self.cases}
        rubrics = {(r.id, r.version): r for r in self.rubrics}
        terms = {(t.id, t.version): t for t in self.terminology_sets}
        for clinical_case in self.cases:
            if {s.source_id for s in clinical_case.sources} - set(sources):
                raise ValueError("Référence source inconnue")
            protocol_source = sources.get(clinical_case.protocol_source_id)
            if protocol_source is None or protocol_source.source_type != "gold_protocol":
                raise ValueError("La source du protocole gold doit être de type gold_protocol")
        for scenario in self.scenarios:
            case = cases.get((scenario.case.id, scenario.case.version))
            rubric = rubrics.get((scenario.rubric.id, scenario.rubric.version))
            terminology = terms.get((scenario.terminology.id, scenario.terminology.version))
            if case is None or rubric is None or terminology is None:
                raise ValueError("Référence de scénario inconnue")
            if (
                scenario.case_hash != case.content_hash
                or scenario.rubric_hash != rubric.content_hash
                or scenario.terminology_hash != terminology.content_hash
            ):
                raise ValueError("Hash de référence différent du contenu")
            unique(scenario.opening_fact_ids, "faits d'ouverture")
            case_fact_ids = {f.id for f in case.facts}
            if set(scenario.opening_fact_ids) - case_fact_ids:
                raise ValueError("Fait d'ouverture inconnu")
            if {m.fact_id for m in scenario.empathy_moments} - case_fact_ids:
                raise ValueError("Fait inconnu pour un moment d'empathie")
            if {f for s in scenario.anamnesis_sections for f in s.fact_ids} - case_fact_ids:
                raise ValueError("Fait inconnu dans une section d'anamnèse")
            dimensions = {d.id for d in rubric.dimensions}
            expected_method = (
                scenario.practice.scoring_version if scenario.practice else "assessment-weighted-v1"
            )
            if rubric.scoring_version != expected_method:
                raise ValueError("Méthode de rubrique incompatible avec le scénario")
            if (
                scenario.practice is None
                and {item.dimension for item in case.assessment_items} != dimensions
            ):
                raise ValueError("Chaque dimension doit avoir des items, sans dimension inconnue")
            if scenario.practice is not None:
                specification = scenario.practice
                if scenario.phase not in ("arzt_arzt", "fachbegriffe"):
                    raise ValueError("Exercice structuré réservé aux phases médecin/lexique")
                if set(specification.context_fact_ids) - {f.id for f in case.facts}:
                    raise ValueError("Fait de contexte inconnu")
                if {i.dimension for i in specification.assessment_items} != dimensions:
                    raise ValueError("Rubrique incompatible avec les critères de cette phase")
                term_ids = {t.id for t in terminology.entries}
                for question in specification.questions:
                    if question.term_id is not None and question.term_id not in term_ids:
                        raise ValueError("Terme de question inconnu")
                    if scenario.phase == "fachbegriffe" and (
                        question.kind != "term_definition" or question.term_id is None
                    ):
                        raise ValueError(
                            "Fachbegriffe exige une question liée au lexique versionné"
                        )
                    if scenario.phase == "arzt_arzt" and (
                        question.kind == "term_definition" or question.term_id is not None
                    ):
                        raise ValueError(
                            "Questions de présentation distinctes de l'exercice lexical"
                        )
                if scenario.phase == "arzt_arzt" and (
                    not specification.context_fact_ids
                    or specification.questions[0].kind != "presentation"
                ):
                    raise ValueError("Présentation initiale et contexte source requis")
                if scenario.phase == "fachbegriffe" and dimensions != {"lexical"}:
                    raise ValueError("Score lexical séparé des autres dimensions")
        return self
