"""Cases of an evaluation dataset, projected as the runtime `MedicalCase` the services use.

A dataset carries its own case definitions (`cases.json`) so a dataset version is
self-contained and hashed as a whole. Committed datasets only hold invented cases.
"""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ari.domain.clinical import AnamnesisSectionId
from ari.domain.models import (
    AnamnesisSectionSpec,
    AssessmentItem,
    DisclosureRule,
    EducationalTarget,
    MedicalCase,
    MedicalFact,
    RubricCriterion,
)

NonEmpty = Annotated[str, Field(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvalFact(_Strict):
    id: NonEmpty
    category: NonEmpty
    value: NonEmpty
    patient_phrase: NonEmpty
    disclosure: DisclosureRule
    criticality: str = "normal"


class EvalCriterion(_Strict):
    id: NonEmpty
    label: NonEmpty
    max_score: Annotated[int, Field(ge=1, le=5)] = 5
    description: NonEmpty


class EvalAssessmentItem(_Strict):
    id: NonEmpty
    label: NonEmpty
    satisfied_by_fact_ids: list[NonEmpty] = Field(min_length=1)
    dimension: NonEmpty = "clinical_coverage"
    required: bool = False
    criticality: str = "normal"


class EvalSection(_Strict):
    id: AnamnesisSectionId
    label: NonEmpty
    fact_ids: list[NonEmpty] = Field(min_length=1)


class EvalCase(_Strict):
    id: NonEmpty
    version: NonEmpty
    language: NonEmpty = "de-DE"
    title: NonEmpty
    opening_statement: NonEmpty
    opening_fact_ids: list[NonEmpty] = Field(default_factory=list)
    communication_style: NonEmpty
    unknown_response: NonEmpty
    out_of_scope_response: NonEmpty
    facts: list[EvalFact] = Field(min_length=1)
    rubric: list[EvalCriterion] = Field(min_length=1)
    assessment_items: list[EvalAssessmentItem] = Field(default_factory=list)
    anamnesis_sections: list[EvalSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_resolve(self) -> EvalCase:
        fact_ids = [fact.id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError(f"{self.id}: duplicate fact id")
        known = set(fact_ids)
        referenced = [
            *self.opening_fact_ids,
            *(f for item in self.assessment_items for f in item.satisfied_by_fact_ids),
            *(f for section in self.anamnesis_sections for f in section.fact_ids),
        ]
        unknown = sorted(set(referenced) - known)
        if unknown:
            raise ValueError(f"{self.id}: unknown fact ids {unknown}")
        dimensions = {criterion.id for criterion in self.rubric}
        stray = sorted({i.dimension for i in self.assessment_items} - dimensions)
        if stray:
            raise ValueError(f"{self.id}: assessment items on unknown dimensions {stray}")
        return self

    def to_medical_case(self) -> MedicalCase:
        content_hash = hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        return MedicalCase(
            id=self.id,
            version=self.version,
            validation_status="synthetic",
            content_hash=content_hash,
            language=self.language,
            transcription_context="",
            unknown_response=self.unknown_response,
            out_of_scope_response=self.out_of_scope_response,
            title=self.title,
            public_summary="",
            difficulty="",
            educational_target=EducationalTarget(
                exam="FSP", phase="arzt_patient", duration_minutes=20
            ),
            source_revealed_fact_ids=MappingProxyType(
                {"opening_statement": tuple(self.opening_fact_ids)}
            ),
            opening_statement=self.opening_statement,
            communication_style=self.communication_style,
            facts=tuple(
                MedicalFact(
                    id=fact.id,
                    category=fact.category,
                    value=fact.value,
                    patient_phrase=fact.patient_phrase,
                    disclosure=fact.disclosure,
                    criticality=fact.criticality,
                )
                for fact in self.facts
            ),
            rubric_version=f"evals:{self.id}@{self.version}",
            rubric=tuple(
                RubricCriterion(c.id, c.label, c.max_score, c.description) for c in self.rubric
            ),
            assessment_items=tuple(
                AssessmentItem(
                    id=item.id,
                    label=item.label,
                    satisfied_by_fact_ids=tuple(item.satisfied_by_fact_ids),
                    required=item.required,
                    criticality=item.criticality,
                    dimension=item.dimension,
                )
                for item in self.assessment_items
            ),
            anamnesis_sections=tuple(
                AnamnesisSectionSpec(s.id, s.label, tuple(s.fact_ids))
                for s in self.anamnesis_sections
            ),
        )


class EvalCaseFile(_Strict):
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> EvalCaseFile:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case id")
        return self
