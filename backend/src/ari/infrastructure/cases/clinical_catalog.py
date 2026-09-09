import json
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.orm import Session

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import (
    AssessmentItem,
    CaseMode,
    ConversationSession,
    EducationalTarget,
    MedicalCase,
    MedicalFact,
    RubricCriterion,
    VocabularyHint,
    VocabularyHintAsset,
)
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.loader import CaseCatalog
from ari.infrastructure.persistence.clinical_rows import ScenarioRow, scenario_snapshot


class ClinicalCatalog:
    """Published runtime cases plus explicitly unvalidated historical compatibility cases."""

    def __init__(self, legacy: CaseCatalog, store: ClinicalStore) -> None:
        self.legacy = legacy
        self.store = store

    def list(self) -> tuple[MedicalCase, ...]:
        with Session(self.store.engine) as db:
            rows = db.scalars(
                select(ScenarioRow)
                .where(
                    ScenarioRow.status == "published",
                    ScenarioRow.phase == "arzt_patient",
                )
                .order_by(ScenarioRow.case_id, ScenarioRow.case_version)
            ).all()
            return self.legacy.list() + tuple(self._runtime(db, row) for row in rows)

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
        try:
            return self.legacy.get(
                case_id, version, scenario_id=scenario_id, scenario_version=scenario_version
            )
        except NotFoundError:
            pass
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
            row = rows[0] if rows else None
            if row is None:
                raise NotFoundError("Cas non publié ou phase indisponible")
            return self._runtime(db, row)

    def _runtime(self, db: Session, row: ScenarioRow) -> MedicalCase:
        if row.case_id in {c.id for c in self.legacy.list()}:
            raise InvalidStateError("Collision avec un identifiant historique")
        bundle = self.store._bundle(db, row)
        case, scenario, rubric = bundle.cases[0], bundle.scenarios[0], bundle.rubrics[0]
        return MedicalCase(
            id=case.id,
            version=case.version,
            mode=CaseMode.FSP,
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
            demographics=MappingProxyType({}),
            demographic_responses=MappingProxyType({}),
            source_revealed_fact_ids=MappingProxyType(
                {
                    "opening_statement": scenario.opening_fact_ids,
                }
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
            schema_version=case.schema_version,
            source_refs=tuple(s.source_id for s in case.sources),
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
        )

    def vocabulary_for_session(self, session: ConversationSession) -> VocabularyHintAsset:
        snapshot = session.training_snapshot
        with Session(self.store.engine) as db:
            row = db.get(
                ScenarioRow, (snapshot.get("scenario_id"), snapshot.get("scenario_version"))
            )
            if row is None or scenario_snapshot(row) != dict(snapshot):
                raise InvalidStateError("Référence de lexique de session invalide")
            bundle = self.store._bundle(db, row)
            terminology = bundle.terminology_sets[0]
            return VocabularyHintAsset(
                id=terminology.id,
                version=f"{terminology.id}@{terminology.version}",
                case_id=session.case_id,
                case_version=session.case_version,
                language=bundle.cases[0].language,
                translation_language="fr",
                hints=tuple(
                    VocabularyHint(id=t.id, term=t.german, translation=t.french or "")
                    for t in terminology.entries
                ),
            )
