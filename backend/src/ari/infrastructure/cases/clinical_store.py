"""Transactional local case registry. Imported content is never a workflow command."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.domain.clinical import (
    CaseReview,
    ClinicalBundle,
    ClinicalCaseVersion,
    ClinicalModel,
    RawCaseSource,
    RubricVersion,
    TerminologySetVersion,
    TrainingScenarioVersion,
)
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import new_id, utc_now
from ari.infrastructure.persistence.clinical_rows import (
    CaseSourceRow,
    ClinicalCaseRow,
    PublicationEventRow,
    ReviewRow,
    RubricRow,
    ScenarioRow,
    SourceRow,
    TerminologyRow,
)


def decode[ModelT: ClinicalModel](model: type[ModelT], payload: dict[str, Any]) -> ModelT:
    return model.model_validate_json(json.dumps(payload, allow_nan=False))


class ClinicalStore:
    def __init__(self, engine: Engine, reserved_case_ids: frozenset[str] = frozenset()) -> None:
        self.engine = engine
        self.reserved_case_ids = reserved_case_ids | {
            "ARI-FSP-001",
            "ARI-FSP-001-EN",
            "fsp-abdominal-pain",
            "technical-abdominal-pain-en",
        }

    def import_bundle(self, bundle: ClinicalBundle, *, dry_run: bool = False) -> dict[str, int]:
        # Revalidate even callers using unchecked model_copy/model_construct.
        bundle = decode(ClinicalBundle, bundle.model_dump(mode="json"))
        if {case.id for case in bundle.cases} & self.reserved_case_ids:
            raise InvalidStateError("Un identifiant historique ne peut pas être remplacé")
        counts = {"cas_nouveaux": 0, "cas_identiques": 0, "scenarios_nouveaux": 0}
        database = self.engine.url.database
        if (
            dry_run
            and self.engine.dialect.name == "sqlite"
            and database
            and database != ":memory:"
            and not Path(database).exists()
        ):
            # Merely connecting to SQLite would create the file, violating dry-run.
            return {
                "cas_nouveaux": len(bundle.cases),
                "cas_identiques": 0,
                "scenarios_nouveaux": len(bundle.scenarios),
            }
        try:
            with Session(self.engine) as db, db.begin():
                groups: Sequence[tuple[type[Any], Sequence[ClinicalModel]]] = (
                    (SourceRow, bundle.sources),
                    (RubricRow, bundle.rubrics),
                    (TerminologyRow, bundle.terminology_sets),
                    (ClinicalCaseRow, bundle.cases),
                )
                for row_type, models in groups:
                    for model in models:
                        payload = model.model_dump(mode="json")
                        identity = (
                            (payload["id"], payload["version"])
                            if "version" in payload
                            else payload["id"]
                        )
                        existing = db.get(row_type, identity)
                        if existing is not None:
                            if existing.content_hash != model.content_hash:
                                raise InvalidStateError(f"Conflit de version: {identity}")
                            if row_type is ClinicalCaseRow:
                                counts["cas_identiques"] += 1
                            continue
                        if row_type is ClinicalCaseRow:
                            counts["cas_nouveaux"] += 1
                        if not dry_run:
                            values = {
                                "id": payload["id"],
                                "content_hash": model.content_hash,
                                "payload": payload,
                            }
                            if "version" in payload:
                                values["version"] = payload["version"]
                            db.add(row_type(**values))
                    if not dry_run:
                        db.flush()
                if not dry_run:
                    for case in bundle.cases:
                        for source in case.sources:
                            key = (case.id, case.version, source.source_id)
                            if db.get(CaseSourceRow, key) is None:
                                db.add(
                                    CaseSourceRow(
                                        case_id=case.id,
                                        case_version=case.version,
                                        source_id=source.source_id,
                                    )
                                )
                    db.flush()
                for scenario in bundle.scenarios:
                    existing_scenario = db.get(ScenarioRow, (scenario.id, scenario.version))
                    if existing_scenario is not None:
                        if existing_scenario.content_hash != scenario.content_hash:
                            raise InvalidStateError("Conflit de version de scénario")
                        continue
                    counts["scenarios_nouveaux"] += 1
                    if not dry_run:
                        db.add(
                            ScenarioRow(
                                id=scenario.id,
                                version=scenario.version,
                                content_hash=scenario.content_hash,
                                payload=scenario.model_dump(mode="json"),
                                case_id=scenario.case.id,
                                case_version=scenario.case.version,
                                case_hash=scenario.case_hash,
                                rubric_id=scenario.rubric.id,
                                rubric_version=scenario.rubric.version,
                                rubric_hash=scenario.rubric_hash,
                                terminology_id=scenario.terminology.id,
                                terminology_version=scenario.terminology.version,
                                terminology_hash=scenario.terminology_hash,
                                phase=scenario.phase,
                                status="draft_unvalidated",
                            )
                        )
        except IntegrityError as exc:
            raise InvalidStateError(
                "Conflit concurrent ou référence invalide; import annulé"
            ) from exc
        return counts

    @staticmethod
    def _scenario(
        db: Session, scenario_id: str, version: str, *, lock: bool = False
    ) -> ScenarioRow:
        if lock:
            # An UPDATE also serializes writers on SQLite (SELECT FOR UPDATE would not).
            db.execute(
                update(ScenarioRow)
                .where(
                    ScenarioRow.id == scenario_id,
                    ScenarioRow.version == version,
                )
                .values(status=ScenarioRow.status)
            )
        row = db.get(ScenarioRow, (scenario_id, version), populate_existing=True)
        if row is None:
            raise NotFoundError("Scénario inconnu")
        return row

    @staticmethod
    def _bundle(db: Session, row: ScenarioRow) -> ClinicalBundle:
        case = db.get(ClinicalCaseRow, (row.case_id, row.case_version))
        rubric = db.get(RubricRow, (row.rubric_id, row.rubric_version))
        terminology = db.get(TerminologyRow, (row.terminology_id, row.terminology_version))
        if case is None or rubric is None or terminology is None:
            raise InvalidStateError("Ressource référencée absente")
        clinical_case = decode(ClinicalCaseVersion, case.payload)
        sources = []
        for ref in clinical_case.sources:
            source = db.get(SourceRow, ref.source_id)
            if source is None:
                raise InvalidStateError("Source absente")
            sources.append(decode(RawCaseSource, source.payload))
            if source.content_hash != sources[-1].content_hash:
                raise InvalidStateError("Intégrité de la source invalide")
        bundle = ClinicalBundle(
            sources=tuple(sources),
            cases=(clinical_case,),
            rubrics=(decode(RubricVersion, rubric.payload),),
            terminology_sets=(decode(TerminologySetVersion, terminology.payload),),
            scenarios=(decode(TrainingScenarioVersion, row.payload),),
        )
        if bundle.scenarios[0].content_hash != row.content_hash:
            raise InvalidStateError("Intégrité du scénario invalide")
        for resource, model in (
            (case, bundle.cases[0]),
            (rubric, bundle.rubrics[0]),
            (terminology, bundle.terminology_sets[0]),
        ):
            if resource.content_hash != model.content_hash:
                raise InvalidStateError("Intégrité de la ressource invalide")
        return bundle

    def inspect(self, scenario_id: str, version: str) -> dict[str, Any]:
        with Session(self.engine) as db:
            row = self._scenario(db, scenario_id, version)
            bundle = self._bundle(db, row)
            return {
                "status": row.status,
                "bundle": bundle.model_dump(mode="json"),
                "case_hash": row.case_hash,
                "scenario_hash": row.content_hash,
                "blockers": self._blockers(db, row, bundle),
                "reviews": [r.payload for r in self._reviews(db, row)],
            }

    @staticmethod
    def _reviews(db: Session, row: ScenarioRow) -> Sequence[ReviewRow]:
        return db.scalars(
            select(ReviewRow)
            .where(
                ReviewRow.scenario_id == row.id,
                ReviewRow.scenario_version == row.version,
            )
            .order_by(ReviewRow.sequence)
        ).all()

    def _blockers(self, db: Session, row: ScenarioRow, bundle: ClinicalBundle) -> list[str]:
        blockers = list(bundle.cases[0].blockers)
        executable_practice = (
            row.phase in ("arzt_arzt", "fachbegriffe")
            and bundle.scenarios[0].practice is not None
        )
        if row.phase != "arzt_patient" and not executable_practice:
            blockers.append("Phase modélisée mais non disponible à l'exécution")
        for source in bundle.sources:
            if source.rights != "compatible":
                blockers.append(f"Droits non compatibles ou inconnus: {source.id}")
        latest = {}
        for review_row in self._reviews(db, row):
            review = decode(CaseReview, review_row.payload)
            if review.case_hash == row.case_hash and review.scenario_hash == row.content_hash:
                latest[review.review_type] = review.decision
        for kind in ("clinical", "linguistic"):
            if latest.get(kind) != "approve":
                blockers.append(f"Approbation humaine {kind} manquante pour le contenu exact")
        return blockers

    def record_review(self, review: CaseReview) -> None:
        review = decode(CaseReview, review.model_dump(mode="json"))
        with Session(self.engine) as db, db.begin():
            row = self._scenario(db, review.scenario.id, review.scenario.version, lock=True)
            if row.status != "draft_unvalidated":
                raise InvalidStateError(
                    "Retirer une publication avant de créer une version corrigée"
                )
            if (review.case.id, review.case.version, review.case_hash, review.scenario_hash) != (
                row.case_id,
                row.case_version,
                row.case_hash,
                row.content_hash,
            ):
                raise InvalidStateError("La revue ne correspond pas au contenu exact")
            db.add(
                ReviewRow(
                    id=review.id,
                    scenario_id=row.id,
                    scenario_version=row.version,
                    scenario_hash=row.content_hash,
                    payload=review.model_dump(mode="json"),
                )
            )

    def publish(self, scenario_id: str, version: str, *, actor: str) -> None:
        if not actor.strip():
            raise InvalidStateError("Identité déclarée requise")
        with Session(self.engine) as db, db.begin():
            row = self._scenario(db, scenario_id, version, lock=True)
            if row.status != "draft_unvalidated":
                raise InvalidStateError("Le scénario n'est plus un brouillon")
            bundle = self._bundle(db, row)
            blockers = self._blockers(db, row, bundle)
            if blockers:
                raise InvalidStateError("Publication refusée: " + "; ".join(blockers))
            previous = db.scalars(
                select(ScenarioRow)
                .where(
                    ScenarioRow.case_id == row.case_id,
                    ScenarioRow.case_version == row.case_version,
                    ScenarioRow.phase == row.phase,
                    ScenarioRow.status == "published",
                )
                .with_for_update()
            ).all()
            for prior in previous:
                prior.status = "withdrawn"
                self._audit(db, prior, "superseded", actor)
            db.flush()
            row.status = "published"
            self._audit(db, row, "publish", actor)

    def withdraw(self, scenario_id: str, version: str, *, actor: str) -> None:
        if not actor.strip():
            raise InvalidStateError("Identité déclarée requise")
        with Session(self.engine) as db, db.begin():
            row = self._scenario(db, scenario_id, version, lock=True)
            if row.status != "published":
                raise InvalidStateError("Seul un scénario publié peut être retiré")
            row.status = "withdrawn"
            self._audit(db, row, "withdraw", actor)

    @staticmethod
    def _audit(db: Session, row: ScenarioRow, action: str, actor: str) -> None:
        db.add(
            PublicationEventRow(
                id=new_id(),
                scenario_id=row.id,
                scenario_version=row.version,
                payload={
                    "action": action,
                    "actor_declared": actor,
                    "at": utc_now().isoformat(),
                    "case_hash": row.case_hash,
                    "scenario_hash": row.content_hash,
                },
            )
        )
