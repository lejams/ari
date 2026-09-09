import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ari.domain.errors import CaseValidationError, NotFoundError
from ari.domain.models import (
    AssessmentItem,
    CaseMode,
    DisclosureRule,
    EducationalTarget,
    MedicalCase,
    MedicalFact,
    RubricCriterion,
)
from ari.infrastructure.cases.yaml_io import read_yaml


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FactData(_StrictModel):
    id: str
    category: str
    value: str
    patient_phrase: str
    disclosure: DisclosureRule
    polarity: str = "present"
    criticality: str = "normal"
    patient_phrase_variants: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class _AssessmentItemData(_StrictModel):
    id: str
    label: str
    satisfied_by_fact_ids: list[str] = Field(min_length=1)
    required: bool = False
    weight: float = Field(default=1.0, gt=0)
    criticality: str = "normal"


class _EducationalTargetData(_StrictModel):
    exam: str
    phase: str
    duration_minutes: int = Field(ge=1, le=240)


class _CriterionData(_StrictModel):
    id: str
    label: str
    max_score: int = Field(ge=1, le=5)
    description: str


class _RubricData(_StrictModel):
    version: str
    criteria: list[_CriterionData]


class _CaseData(_StrictModel):
    schema_version: str = "clinical-case-v1"
    id: str
    version: str
    mode: CaseMode
    validation_status: str
    language: str
    transcription_context: str
    unknown_response: str
    out_of_scope_response: str
    title: str
    public_summary: str
    difficulty: str
    educational_target: _EducationalTargetData
    demographics: dict[str, str | int | float | bool]
    demographic_responses: dict[str, str]
    source_revealed_fact_ids: dict[str, list[str]]
    opening_statement: str
    communication_style: str
    facts: list[_FactData]
    rubric: _RubricData
    source_collection: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    assessment_items: list[_AssessmentItemData] = Field(default_factory=list)


class CaseCatalog:
    def __init__(self, cases: tuple[MedicalCase, ...]) -> None:
        self._cases = {(item.id, item.version): item for item in cases}
        if len(self._cases) != len(cases):
            raise CaseValidationError("Duplicate case id/version")

    def list(self) -> tuple[MedicalCase, ...]:
        return tuple(self._cases.values())

    def get(
        self, case_id: str, version: str, *, scenario_id: str | None = None,
        scenario_version: str | None = None,
    ) -> MedicalCase:
        if scenario_id is not None or scenario_version is not None:
            raise NotFoundError("Historical cases have no versioned training scenario")
        try:
            return self._cases[(case_id, version)]
        except KeyError as exc:
            raise NotFoundError(f"Unknown case {case_id}@{version}") from exc


def load_cases(directory: Path, *, include_technical_test: bool = False) -> CaseCatalog:
    loaded: list[MedicalCase] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = read_yaml(path.read_text(encoding="utf-8"))
            data = _CaseData.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
            raise CaseValidationError(f"Invalid case file {path.name}: {exc}") from exc

        fact_ids = [fact.id for fact in data.facts]
        criterion_ids = [criterion.id for criterion in data.rubric.criteria]
        assessment_ids = [item.id for item in data.assessment_items]
        if len(fact_ids) != len(set(fact_ids)):
            raise CaseValidationError(f"Duplicate fact id in {path.name}")
        if len(criterion_ids) != len(set(criterion_ids)):
            raise CaseValidationError(f"Duplicate rubric criterion in {path.name}")
        if len(assessment_ids) != len(set(assessment_ids)):
            raise CaseValidationError(f"Duplicate assessment item id in {path.name}")
        duplicated_assessment_facts = {
            item.id
            for item in data.assessment_items
            if len(item.satisfied_by_fact_ids) != len(set(item.satisfied_by_fact_ids))
        }
        if duplicated_assessment_facts:
            raise CaseValidationError(
                f"Duplicate assessment fact reference in {path.name}: "
                f"{sorted(duplicated_assessment_facts)}"
            )
        allowed_non_fact_sources = {
            "opening_statement",
            *(f"demographics.{key}" for key in data.demographic_responses),
        }
        unknown_sources = set(data.source_revealed_fact_ids) - allowed_non_fact_sources
        if unknown_sources:
            raise CaseValidationError(
                f"Unknown source reference in {path.name}: {sorted(unknown_sources)}"
            )
        duplicated_source_facts = {
            source
            for source, ids in data.source_revealed_fact_ids.items()
            if len(ids) != len(set(ids))
        }
        if duplicated_source_facts:
            raise CaseValidationError(
                f"Duplicate revealed fact in {path.name}: {sorted(duplicated_source_facts)}"
            )
        unknown_revealed_facts = {
            fact_id
            for ids in data.source_revealed_fact_ids.values()
            for fact_id in ids
            if fact_id not in fact_ids
        }
        if unknown_revealed_facts:
            raise CaseValidationError(
                f"Unknown revealed fact in {path.name}: {sorted(unknown_revealed_facts)}"
            )
        unknown_assessment_facts = {
            fact_id
            for item in data.assessment_items
            for fact_id in item.satisfied_by_fact_ids
            if fact_id not in fact_ids
        }
        if unknown_assessment_facts:
            raise CaseValidationError(
                f"Unknown assessment fact in {path.name}: {sorted(unknown_assessment_facts)}"
            )

        canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        case = MedicalCase(
            id=data.id,
            version=data.version,
            mode=data.mode,
            validation_status=data.validation_status,
            content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            language=data.language,
            transcription_context=data.transcription_context,
            unknown_response=data.unknown_response,
            out_of_scope_response=data.out_of_scope_response,
            title=data.title,
            public_summary=data.public_summary,
            difficulty=data.difficulty,
            educational_target=EducationalTarget(**data.educational_target.model_dump()),
            demographics=MappingProxyType(dict(data.demographics)),
            demographic_responses=MappingProxyType(dict(data.demographic_responses)),
            source_revealed_fact_ids=MappingProxyType(
                {source: tuple(ids) for source, ids in data.source_revealed_fact_ids.items()}
            ),
            opening_statement=data.opening_statement,
            communication_style=data.communication_style,
            facts=tuple(
                MedicalFact(
                    **item.model_dump(exclude={"patient_phrase_variants", "source_refs"}),
                    patient_phrase_variants=tuple(item.patient_phrase_variants),
                    source_refs=tuple(item.source_refs),
                )
                for item in data.facts
            ),
            rubric_version=data.rubric.version,
            rubric=tuple(RubricCriterion(**item.model_dump()) for item in data.rubric.criteria),
            schema_version=data.schema_version,
            source_collection=data.source_collection,
            source_refs=tuple(data.source_refs),
            assessment_items=tuple(
                AssessmentItem(
                    **item.model_dump(exclude={"satisfied_by_fact_ids"}),
                    satisfied_by_fact_ids=tuple(item.satisfied_by_fact_ids),
                )
                for item in data.assessment_items
            ),
        )
        if case.mode is CaseMode.FSP or include_technical_test:
            loaded.append(case)
    if not loaded:
        raise CaseValidationError(f"No medical cases found in {directory}")
    return CaseCatalog(tuple(loaded))
