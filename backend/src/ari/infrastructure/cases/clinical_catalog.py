"""Published registry scenarios projected as runtime cases."""

import json
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.orm import Session

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import (
    AnamnesisSectionSpec,
    AssessmentItem,
    EducationalTarget,
    EmpathyMomentSpec,
    MedicalCase,
    MedicalFact,
    RubricCriterion,
    TerminologyTerm,
)
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.persistence.platform.clinical_rows import ScenarioRow, scenario_snapshot


class ClinicalCatalog:
    def __init__(self, store: ClinicalStore) -> None:
        self.store = store

    def list(self) -> tuple[MedicalCase, ...]:
        with Session(self.store.engine) as db:
            rows = db.scalars(
                select(ScenarioRow)
                .where(ScenarioRow.status == "published", ScenarioRow.phase == "arzt_patient")
                .order_by(ScenarioRow.case_id, ScenarioRow.case_version)
            ).all()
            return tuple(self._runtime(db, row) for row in rows)

    def get(
        self,
        case_id: str,
        version: str,
        *,
        scenario_id: str | None = None,
        scenario_version: str | None = None,
    ) -> MedicalCase:
        if (scenario_id is None) != (scenario_version is None):
            raise InvalidStateError("Identifiant et version du scénario requis ensemble")
        with Session(self.store.engine) as db:
            statement = select(ScenarioRow).where(
                ScenarioRow.case_id == case_id,
                ScenarioRow.case_version == version,
                ScenarioRow.phase == "arzt_patient",
                ScenarioRow.status.in_(("published", "withdrawn")),
            )
            if scenario_id is not None:
                statement = statement.where(
                    ScenarioRow.id == scenario_id, ScenarioRow.version == scenario_version
                )
            rows = db.scalars(statement).all()
            if scenario_id is None:
                published = [row for row in rows if row.status == "published"]
                rows = published or rows
            if len(rows) > 1:
                raise InvalidStateError("Plusieurs scénarios: préciser identifiant et version")
            if not rows:
                raise NotFoundError("Cas non publié ou phase indisponible")
            return self._runtime(db, rows[0])

    def _runtime(self, db: Session, row: ScenarioRow) -> MedicalCase:
        bundle = self.store._bundle(db, row)
        case, scenario, rubric = bundle.cases[0], bundle.scenarios[0], bundle.rubrics[0]
        terminology = bundle.terminology_sets[0]
        return MedicalCase(
            id=case.id,
            version=case.version,
            validation_status=row.status,
            content_hash=case.content_hash,
            language=case.language,
            transcription_context=case.transcription_context,
            unknown_response=case.unknown_response,
            out_of_scope_response=case.out_of_scope_response,
            title=case.title,
            public_summary=case.public_summary,
            difficulty=scenario.difficulty,
            educational_target=EducationalTarget("FSP", scenario.phase, scenario.duration_minutes),
            source_revealed_fact_ids=MappingProxyType(
                {"opening_statement": scenario.opening_fact_ids}
            ),
            opening_statement=scenario.opening,
            communication_style=scenario.persona,
            facts=tuple(
                MedicalFact(
                    id=f.id,
                    category=f.category,
                    value=(
                        f.patient_phrases_de[0]
                        if f.polarity == "unknown"
                        else f.value
                        if isinstance(f.value, str)
                        else json.dumps(f.value)
                    )
                    + (f" {f.unit}" if f.unit else ""),
                    patient_phrase=f.patient_phrases_de[0],
                    disclosure=f.disclosure,
                    polarity=f.polarity,
                    criticality=f.criticality,
                    patient_phrase_variants=f.patient_phrases_de[1:],
                    source_refs=tuple(s.source_id for s in f.sources),
                )
                for f in case.facts
            ),
            rubric_version=f"{rubric.id}@{rubric.version}",
            rubric=tuple(RubricCriterion(**d.model_dump()) for d in rubric.dimensions),
            assessment_items=tuple(
                AssessmentItem(
                    id=i.id,
                    label=i.expected_behavior,
                    satisfied_by_fact_ids=i.satisfied_by_fact_ids,
                    required=i.required,
                    weight=i.weight,
                    dimension=i.dimension,
                    evidence_kind=i.evidence_kind,
                    satisfaction=i.satisfaction,
                    doctor_phrases=i.doctor_phrases,
                )
                for i in case.assessment_items
            ),
            training_snapshot=MappingProxyType(scenario_snapshot(row)),
            available_for_new_sessions=row.status == "published",
            terminology=tuple(
                TerminologyTerm(id=t.id, german=t.german, french=t.french)
                for t in terminology.entries
            ),
            empathy_moments=tuple(
                EmpathyMomentSpec(id=m.id, fact_id=m.fact_id, cue=m.cue_fr, expected=m.expected_fr)
                for m in scenario.empathy_moments
            ),
            anamnesis_sections=tuple(
                AnamnesisSectionSpec(id=s.id, label=s.label_de, fact_ids=s.fact_ids)
                for s in scenario.anamnesis_sections
            ),
            cefr=scenario.cefr,
            land=case.location.land,
            city=case.location.city,
        )
