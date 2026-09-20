"""Gold protocol → training bundle: deterministic skeleton, model-written text, strict assembly.

The structure of a case never comes from the model. The generator derives identifiers, facts,
sections, assessment items, practice questions and rubrics from the gold protocol alone; the
model only writes what a patient says, translations, coaching and accepted answers, against the
ids of that skeleton. Anything it adds, drops or misnames makes the draft `invalid`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import ValidationError

from ari.content.domain.bundles import PHASES, BundleVariantRequest, Phase
from ari.content.domain.documents import Document
from ari.content.domain.protocol import AnamnesisItem, GoldProtocol
from ari.content.schemas import BundleDraftOutput
from ari.content.services.rubrics import (
    ANAMNESIS_RUBRIC,
    ARZT_ARZT_RUBRIC,
    FACHBEGRIFFE_RUBRIC,
    REQUIRED_SECTIONS,
    SECTION_LABELS_DE,
)
from ari.domain.clinical import (
    AnamnesisSectionId,
    ClinicalBundle,
    ClinicalCaseVersion,
    ClinicalModel,
    RawCaseSource,
    TerminologySetVersion,
    TrainingScenarioVersion,
)
from ari.domain.errors import InvalidStateError
from ari.domain.geography import LAND_CODES

QuestionKind = Literal["presentation", "followup", "term_definition"]

# Communication behaviours every anamnesis scenario assesses; the model supplies the phrases.
BEHAVIOUR_ITEMS: tuple[tuple[str, str], ...] = (
    ("greeting", "Saluer le patient"),
    ("introduction", "Se présenter comme médecin"),
    ("closing", "Résumer les points recueillis et conclure l'entretien"),
)
PRESENTATION_QUESTION_ID = "presentation"
PERSONA_INSTRUCTIONS: dict[str, str] = {
    "standard": "kooperativ, antwortet in ganzen Sätzen, ergänzt nichts von sich aus",
    "anxious": "ängstlich, fragt nach, braucht Beruhigung, antwortet knapp",
    "talkative": "redselig, schweift ab, muss zurückgeführt werden",
    "terse": "einsilbig, antwortet nur auf präzise Fragen",
}


class BundleDraftInvalid(Exception):
    """The model's output does not fit the skeleton; the draft is stored with these reasons."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = tuple(errors)


@dataclass(frozen=True, slots=True)
class FactSeed:
    id: str
    section: AnamnesisSectionId
    label_de: str
    value_de: str | None
    polarity: str
    temporality: str | None
    critical: bool
    pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class QuestionSeed:
    id: str
    kind: QuestionKind
    prompt_de: str
    term_id: str | None
    hint_de: str | None  # Expected answer or lay term from the protocol, when known.


@dataclass(frozen=True, slots=True)
class BundleSkeleton:
    case_id: str
    source_id: str
    terminology_id: str
    phases: tuple[Phase, ...]
    facts: tuple[FactSeed, ...]
    behaviour_items: tuple[tuple[str, str], ...]
    arzt_arzt_questions: tuple[QuestionSeed, ...]
    fachbegriffe_questions: tuple[QuestionSeed, ...]

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(fact.id for fact in self.facts)

    def practice_questions(self) -> tuple[QuestionSeed, ...]:
        questions: tuple[QuestionSeed, ...] = ()
        if "arzt_arzt" in self.phases:
            questions += self.arzt_arzt_questions
        if "fachbegriffe" in self.phases:
            questions += self.fachbegriffe_questions
        return questions


def available_phases(gold: GoldProtocol) -> tuple[Phase, ...]:
    """Phases this protocol supports: facts for the anamnesis, asked terms for the lexicon."""
    record = gold.record
    phases: list[Phase] = []
    if record.anamnesis:
        phases += ["arzt_patient", "arzt_arzt"]
    if any(term.asked for term in record.fachbegriffe):
        phases.append("fachbegriffe")
    return tuple(phase for phase in PHASES if phase in phases)


def case_identifier(gold: GoldProtocol) -> str:
    assert gold.location.land is not None  # guaranteed by GoldProtocol
    return f"FSP-{LAND_CODES[gold.location.land]}-{gold.protocol_id}"


def build_skeleton(gold: GoldProtocol, request: BundleVariantRequest) -> BundleSkeleton:
    supported = available_phases(gold)
    missing = [phase for phase in request.phases if phase not in supported]
    if missing:
        raise InvalidStateError("Phases impossibles pour ce protocole : " + ", ".join(missing))
    record = gold.record
    critical_ids = {
        item_id
        for pitfall in record.pedagogy.critical_pitfalls
        for item_id in pitfall.related_item_ids
    }
    case_id = case_identifier(gold)
    facts = tuple(_fact_seed(item, item.id in critical_ids) for item in record.anamnesis)
    who = {"female": "die Patientin", "male": "den Patienten"}.get(
        record.patient.sex, "die Patientin oder den Patienten"
    )
    arzt_arzt = [
        QuestionSeed(
            id=PRESENTATION_QUESTION_ID,
            kind="presentation",
            prompt_de=f"Stellen Sie mir bitte {who} kurz vor.",
            term_id=None,
            hint_de=record.arzt_arzt.presentation_summary_de,
        )
    ]
    arzt_arzt += [
        QuestionSeed(
            id=f"q-{question.id}",
            kind="followup",
            prompt_de=question.question_de,
            term_id=None,
            hint_de=question.expected_answer_de,
        )
        for question in record.examiner_questions
        if question.part in ("arzt_arzt", "general")
    ]
    fachbegriffe = [
        QuestionSeed(
            id=f"term-{term.id}",
            kind="term_definition",
            prompt_de=f"Was bedeutet „{term.german}“ in einfachen Worten?",
            term_id=term.id,
            hint_de=term.lay_german,
        )
        for term in record.fachbegriffe
        if term.asked
    ]
    return BundleSkeleton(
        case_id=case_id,
        source_id=f"{case_id}-protocol",
        terminology_id=f"{case_id}-terms",
        phases=tuple(phase for phase in PHASES if phase in request.phases),
        facts=facts,
        behaviour_items=BEHAVIOUR_ITEMS,
        arzt_arzt_questions=tuple(arzt_arzt),
        fachbegriffe_questions=tuple(fachbegriffe),
    )


def _fact_seed(item: AnamnesisItem, critical: bool) -> FactSeed:
    return FactSeed(
        id=item.id,
        section=item.section,
        label_de=item.label_de,
        value_de=item.value_de,
        polarity=item.polarity,
        temporality=item.temporality,
        critical=critical,
        pages=item.source_pages,
    )


def model_payload(
    skeleton: BundleSkeleton, gold: GoldProtocol, request: BundleVariantRequest
) -> dict[str, Any]:
    """What the model receives: the skeleton's ids and the protocol's clinical content, no PII."""
    record = gold.record
    return {
        "case_id": skeleton.case_id,
        "phases": list(skeleton.phases),
        "persona_variant": request.persona_variant,
        "persona_instruction_de": PERSONA_INSTRUCTIONS[request.persona_variant],
        "cefr": request.cefr,
        "language": record.language,
        "patient": record.patient.model_dump(mode="json", exclude={"uncertainty"}),
        "diagnosis": record.diagnosis.model_dump(mode="json", exclude={"uncertainty"}),
        "facts": [asdict(fact) for fact in skeleton.facts],
        "behaviour_items": [
            {"item_id": item_id, "expected_behavior_fr": text}
            for item_id, text in skeleton.behaviour_items
        ],
        "practice_questions": [asdict(question) for question in skeleton.practice_questions()],
        "critical_pitfalls_fr": [p.text_fr for p in record.pedagogy.critical_pitfalls],
        "pitfalls_fr": [p.text_fr for p in record.pedagogy.pitfalls],
        "difficulty": record.pedagogy.difficulty,
        "examiner_feedback_de": list(record.outcome.examiner_feedback_de),
    }


def assemble(
    skeleton: BundleSkeleton,
    gold: GoldProtocol,
    document: Document,
    request: BundleVariantRequest,
    output: BundleDraftOutput,
) -> ClinicalBundle:
    """Merge the model's text into the skeleton and validate the whole bundle, or refuse."""
    errors = _check_ids(skeleton, output)
    if errors:
        raise BundleDraftInvalid(errors)
    fact_text = {fact.fact_id: fact for fact in output.facts}
    behaviour = {item.item_id: item for item in output.behaviour_items}
    answers = {answer.question_id: answer for answer in output.practice_answers}
    version = str(request.revision)
    try:
        source = _decode(RawCaseSource, _source(skeleton, gold, document))
        terminology = _decode(
            TerminologySetVersion,
            {
                "id": skeleton.terminology_id,
                "version": version,
                "entries": [
                    {"id": term.id, "german": term.german, "french": term.french}
                    for term in gold.record.fachbegriffe
                ],
            },
        )
        case = _decode(
            ClinicalCaseVersion, _case(skeleton, gold, output, fact_text, behaviour, version)
        )
        scenarios = []
        refs = {
            "case": {"id": case.id, "version": case.version},
            "case_hash": case.content_hash,
            "terminology": {"id": terminology.id, "version": terminology.version},
            "terminology_hash": terminology.content_hash,
        }
        for phase in skeleton.phases:
            scenarios.append(
                _decode(
                    TrainingScenarioVersion,
                    _scenario(phase, skeleton, gold, request, output, answers, refs, version),
                )
            )
        rubrics = {scenario.rubric.id for scenario in scenarios}
        bundle = _decode(
            ClinicalBundle,
            {
                "sources": [source.model_dump(mode="json")],
                "rubrics": [
                    rubric.model_dump(mode="json")
                    for rubric in (ANAMNESIS_RUBRIC, ARZT_ARZT_RUBRIC, FACHBEGRIFFE_RUBRIC)
                    if rubric.id in rubrics
                ],
                "terminology_sets": [terminology.model_dump(mode="json")],
                "cases": [case.model_dump(mode="json")],
                "scenarios": [scenario.model_dump(mode="json") for scenario in scenarios],
            },
        )
    except ValidationError as exc:
        raise BundleDraftInvalid(
            [
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}".strip(": ")
                for error in exc.errors(include_input=False, include_context=False)
            ]
        ) from exc
    return bundle


def _check_ids(skeleton: BundleSkeleton, output: BundleDraftOutput) -> list[str]:
    errors: list[str] = []
    fact_ids = set(skeleton.fact_ids)
    given = [fact.fact_id for fact in output.facts]
    errors += [f"fait inconnu: {fid}" for fid in given if fid not in fact_ids]
    errors += [f"fait sans phrases patient: {fid}" for fid in skeleton.fact_ids if fid not in given]
    if len(given) != len(set(given)):
        errors.append("faits en double dans la sortie du modèle")
    opening = output.opening_fact_ids
    errors += [f"fait d'ouverture inconnu: {fid}" for fid in opening if fid not in fact_ids]
    if len(output.opening_fact_ids) != len(set(output.opening_fact_ids)):
        errors.append("faits d'ouverture en double")
    empathy = [moment.fact_id for moment in output.empathy_moments]
    errors += [f"moment d'empathie sur un fait inconnu: {f}" for f in empathy if f not in fact_ids]
    if len(empathy) != len(set(empathy)):
        errors.append("deux moments d'empathie sur le même fait")
    if "arzt_patient" in skeleton.phases:
        expected = {item_id for item_id, _ in skeleton.behaviour_items}
        given_items = [item.item_id for item in output.behaviour_items]
        errors += [f"comportement inconnu: {i}" for i in given_items if i not in expected]
        errors += [f"comportement sans phrases: {i}" for i in expected if i not in given_items]
    question_ids = [question.id for question in skeleton.practice_questions()]
    given_answers = [answer.question_id for answer in output.practice_answers]
    errors += [f"question inconnue: {q}" for q in given_answers if q not in question_ids]
    errors += [f"question sans réponses: {q}" for q in question_ids if q not in given_answers]
    if len(given_answers) != len(set(given_answers)):
        errors.append("réponses en double dans la sortie du modèle")
    return errors


def _source(skeleton: BundleSkeleton, gold: GoldProtocol, document: Document) -> dict[str, Any]:
    declaration = document.declaration
    return {
        "id": skeleton.source_id,
        "source_type": "gold_protocol",
        "synthetic": False,
        "document_reference": f"gold:{gold.protocol_id}@{gold.protocol_version}",
        "original_checksum": document.id,
        "immediate_source_checksum": gold.protocol_hash,
        "provenance": declaration.provenance,
        # The freeze instant, not now: a regenerated revision must yield the identical source.
        "imported_at": gold.frozen_at.isoformat(),
        "rights": gold.rights,
        "intended_use": declaration.intended_use,
        "rights_evidence": gold.rights_evidence,
        "private_material_reference": f"content:documents/{document.id}",
        "declared_original": None,
        "original_verified": False,
    }


def _case(
    skeleton: BundleSkeleton,
    gold: GoldProtocol,
    output: BundleDraftOutput,
    fact_text: dict[str, Any],
    behaviour: dict[str, Any],
    version: str,
) -> dict[str, Any]:
    opening = set(output.opening_fact_ids)
    facts = [
        {
            "id": seed.id,
            "category": seed.section,
            "value": seed.value_de,
            "unit": None,
            "temporality": seed.temporality,
            "polarity": seed.polarity,
            "criticality": "critical" if seed.critical else "normal",
            "patient_phrases_de": fact_text[seed.id].patient_phrases_de,
            "translation_fr": fact_text[seed.id].translation_fr,
            "disclosure": "spontaneous" if seed.id in opening else "when_asked",
            "sources": [{"source_id": skeleton.source_id, "pages": list(seed.pages)}],
            "uncertainty": None,
        }
        for seed in skeleton.facts
    ]
    items: list[dict[str, Any]] = []
    for section in SECTION_LABELS_DE:
        ids = [seed.id for seed in skeleton.facts if seed.section == section]
        if ids:
            items.append(
                {
                    "id": f"section-{section}",
                    "dimension": "clinical_coverage",
                    "weight": 2.0 if section == "aktuelle_beschwerden" else 1.0,
                    "required": section in REQUIRED_SECTIONS,
                    "expected_behavior": f"Recueillir : {SECTION_LABELS_DE[section]}",
                    "evidence_kind": "delivered_facts",
                    "satisfaction": "all",
                    "satisfied_by_fact_ids": ids,
                    "doctor_phrases": [],
                }
            )
    critical = [seed.id for seed in skeleton.facts if seed.critical]
    if critical:
        items.append(
            {
                "id": "critical-facts",
                "dimension": "clinical_coverage",
                "weight": 2.0,
                "required": True,
                "expected_behavior": "Recueillir chaque élément signalé comme piège grave "
                "par le médecin relecteur",
                "evidence_kind": "delivered_facts",
                "satisfaction": "all",
                "satisfied_by_fact_ids": critical,
                "doctor_phrases": [],
            }
        )
    items += [
        {
            "id": item_id,
            "dimension": "communication",
            "weight": 1.0,
            "required": False,
            "expected_behavior": text,
            "evidence_kind": "doctor_quote",
            "satisfaction": "any",
            "satisfied_by_fact_ids": [],
            "doctor_phrases": behaviour[item_id].doctor_phrases,
        }
        for item_id, text in skeleton.behaviour_items
    ]
    return {
        "id": skeleton.case_id,
        "version": version,
        "schema_version": "clinical-case-v3",
        "language": gold.record.language,
        "gold_protocol": {
            "protocol_id": gold.protocol_id,
            "protocol_version": str(gold.protocol_version),
            "protocol_hash": gold.protocol_hash,
        },
        "protocol_source_id": skeleton.source_id,
        "location": gold.location.model_dump(mode="json"),
        "title": output.title_de,
        "public_summary": output.public_summary_fr,
        "transcription_context": output.transcription_context_de,
        "unknown_response": output.unknown_response_de,
        "out_of_scope_response": output.out_of_scope_response_de,
        "sources": [{"source_id": skeleton.source_id, "pages": []}],
        "unresolved_questions": [],
        "facts": facts,
        "assessment_items": items,
    }


def _scenario(
    phase: Phase,
    skeleton: BundleSkeleton,
    gold: GoldProtocol,
    request: BundleVariantRequest,
    output: BundleDraftOutput,
    answers: dict[str, Any],
    refs: dict[str, Any],
    version: str,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"{skeleton.case_id}-{phase}",
        "version": version,
        **refs,
        "phase": phase,
        "difficulty": gold.record.pedagogy.difficulty,
        "cefr": request.cefr,
    }
    if phase == "arzt_patient":
        sections = [
            {
                "id": section,
                "label_de": SECTION_LABELS_DE[section],
                "fact_ids": [seed.id for seed in skeleton.facts if seed.section == section],
            }
            for section in SECTION_LABELS_DE
            if any(seed.section == section for seed in skeleton.facts)
        ]
        return {
            **base,
            "rubric": {"id": ANAMNESIS_RUBRIC.id, "version": ANAMNESIS_RUBRIC.version},
            "rubric_hash": ANAMNESIS_RUBRIC.content_hash,
            "persona": output.persona_de,
            "opening": output.opening_de,
            "opening_fact_ids": list(output.opening_fact_ids),
            "objectives": list(output.objectives_fr),
            "duration_minutes": 20,
            "empathy_moments": [
                {
                    "id": f"empathy-{index:02d}",
                    "fact_id": moment.fact_id,
                    "cue_fr": moment.cue_fr,
                    "expected_fr": moment.expected_fr,
                }
                for index, moment in enumerate(output.empathy_moments, start=1)
            ],
            "anamnesis_sections": sections,
        }
    if phase == "arzt_arzt":
        questions = skeleton.arzt_arzt_questions
        rubric, dimension = ARZT_ARZT_RUBRIC, "structured_presentation"
        persona = "Oberärztin oder Oberarzt, kollegiale Fallbesprechung"
        opening = "Bitte stellen Sie mir den Fall vor."
        objective = "Présenter le cas de façon structurée à un confrère, sans rien inventer"
        context = list(skeleton.fact_ids)
        weight = {PRESENTATION_QUESTION_ID: 2.0}
    else:
        questions = skeleton.fachbegriffe_questions
        rubric, dimension = FACHBEGRIFFE_RUBRIC, "lexical"
        persona = "Prüferin oder Prüfer, fragt Fachbegriffe ab"
        opening = "Erklären Sie bitte die folgenden Fachbegriffe in einfachen Worten."
        objective = "Expliquer chaque terme demandé en allemand courant"
        context = []
        weight = {}
    return {
        **base,
        "rubric": {"id": rubric.id, "version": rubric.version},
        "rubric_hash": rubric.content_hash,
        "persona": persona,
        "opening": opening,
        "opening_fact_ids": [],
        "objectives": [objective],
        "duration_minutes": 10 if phase == "arzt_arzt" else 5,
        "practice": {
            "schema_version": "practice-spec-v1",
            "scoring_version": "practice-exact-answer-v1",
            "context_fact_ids": context,
            "questions": [
                {
                    "id": question.id,
                    "kind": question.kind,
                    "prompt_de": question.prompt_de,
                    "coaching_fr": answers[question.id].coaching_fr,
                    "assessment_item_id": question.id,
                    "term_id": question.term_id,
                }
                for question in questions
            ],
            "assessment_items": [
                {
                    "id": question.id,
                    "dimension": dimension,
                    "weight": weight.get(question.id, 1.0),
                    "expected_behavior": answers[question.id].expected_behavior,
                    "accepted_answers": _accepted(answers[question.id].accepted_answers, question),
                    "evidence_rule": "normalized_exact_answer",
                }
                for question in questions
            ],
        },
    }


def _accepted(given: list[str], question: QuestionSeed) -> list[str]:
    """The model's variants plus the protocol's own wording, without duplicates."""
    variants = list(given)
    if question.kind == "term_definition" and question.hint_de:
        variants.append(question.hint_de)
    seen: set[str] = set()
    unique: list[str] = []
    for variant in variants:
        key = variant.casefold().strip()
        if key not in seen:
            seen.add(key)
            unique.append(variant)
    return unique


def _decode[ModelT: ClinicalModel](model: type[ModelT], payload: dict[str, Any]) -> ModelT:
    return model.model_validate_json(json.dumps(payload, ensure_ascii=False, allow_nan=False))
