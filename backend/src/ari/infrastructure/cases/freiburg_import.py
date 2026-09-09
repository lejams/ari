"""Freiburg draft intake: validates, reconciles and preserves; never publishes.

Separate from the executable ARI bundle adapter: no invented rubric, CEFR, opening,
disclosure timing or transcript proof can enter training through this intake.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ari.domain.clinical_privacy import contact_locations
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.infrastructure.cases.freiburg_format import FreiburgExport, WorkCase
from ari.infrastructure.cases.freiburg_workbook import verify_workbook
from ari.infrastructure.cases.yaml_io import read_yaml
from ari.infrastructure.persistence.clinical_rows import DraftBatchRow, DraftCaseRow

MAPPING_VERSION = "freiburg-review-draft-v1"


def load_freiburg(json_path: Path, workbook_path: Path) -> tuple[FreiburgExport, dict[str, Any]]:
    json_bytes, workbook_bytes = json_path.read_bytes(), workbook_path.read_bytes()
    # Duplicate keys are refused before Pydantic's JSON parser can collapse them.
    raw = read_yaml(json_bytes.decode("utf-8"))
    export = FreiburgExport.model_validate_json(json.dumps(raw, allow_nan=False))
    counts = verify_workbook(export, workbook_bytes)
    return export, {
        "json": {
            "path": str(json_path.resolve()),
            "sha256": hashlib.sha256(json_bytes).hexdigest(),
        },
        "xlsx": {
            "path": str(workbook_path.resolve()),
            "sha256": hashlib.sha256(workbook_bytes).hexdigest(),
        },
        "lignes_concordantes": counts,
    }


def review_material(export: FreiburgExport, case: WorkCase) -> dict[str, Any]:
    """Lossless case + relevant shared material; null and unknown remain distinct."""
    return {
        "mapping_version": MAPPING_VERSION,
        "source_schema": export.schema_version,
        "generated_on": export.generated_on,
        "validation_declaree": export.validation.model_dump(mode="json"),
        "case": case.model_dump(mode="json"),
        "sources": [s.model_dump(mode="json") for s in export.sources],
        "questions": [
            q.model_dump(mode="json")
            for q in export.open_questions
            if q.case_id in (None, case.case_id)
        ],
        "privacy_note": export.privacy_note,
        "rights": "unknown",
        "original_verified": False,
        "weights_provisional": True,
        "executable": False,
    }


def material_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def case_blockers(export: FreiburgExport, case: WorkCase) -> list[str]:
    blockers = [
        "Revue clinique humaine non effectuée; diagnostic seulement rapporté.",
        "Revue linguistique DE/FR non effectuée.",
        "Droits d'usage à confirmer; déclaration de licence non vérifiée.",
        "Poids provisoires; rubrique, dimensions, all/any et preuves à faire valider.",
        "Scénario non défini: persona, CEFR, difficulté, objectifs, réponses limites.",
        "Règles de divulgation Work à mapper et valider avant exécution.",
        "Revue de confidentialité: noms de patients rapportés, anonymisation non garantie.",
        *case.open_questions,
    ]
    for question in export.open_questions:
        if question.case_id in (None, case.case_id) and not question.source_pages:
            blockers.append(
                f"Question {question.question_id}: page source absente; "
                "provenance à préciser, aucune page déduite."
            )
    if case.opening_line_de is None:
        blockers.append("Ouverture allemande absente; aucune phrase inventée.")
    if any(a.type == "arzt_arzt_question" for a in case.assessment_items):
        blockers.append("Questions médecin-médecin conservées hors scoring et phase inexécutable.")
    if contact_locations(case.model_dump(mode="json")):
        blockers.append("Coordonnées potentielles détectées: matériau privé, nettoyage requis.")
    return blockers


def validation_report(export: FreiburgExport, receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "contrat": export.schema_version,
        "mapping": MAPPING_VERSION,
        "validation_structurelle": "valide; ne vaut pas validation médicale",
        "fichiers_recus": receipt,
        "lot_hash": export.content_hash,
        "cas_valides_structurellement": len(export.cases),
        "cas_importes_cette_operation": 0,
        "scenarios_executables": 0,
        "revues_cliniques": 0,
        "revues_linguistiques": 0,
        "droits_confirmes": 0,
        "publication": "non exécutée et impossible depuis cette zone",
        "checksum_pdf_verifie": None,
        "poids_provisoires": True,
        "criteres_information": sum(
            a.type == "information_gathering" for c in export.cases for a in c.assessment_items
        ),
        "questions_medecin_medecin_hors_scoring": sum(
            a.type == "arzt_arzt_question" for c in export.cases for a in c.assessment_items
        ),
        "cas": [
            {
                "id": c.case_id,
                "version": c.version,
                "hash": material_hash(review_material(export, c)),
                "status": "draft_unvalidated",
                "blocages": case_blockers(export, c),
            }
            for c in export.cases
        ],
    }


class FreiburgDraftStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def import_export(
        self,
        export: FreiburgExport,
        receipt: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        export = FreiburgExport.model_validate_json(export.model_dump_json())
        materials = {c.case_id: material_hash(review_material(export, c)) for c in export.cases}
        missing_database = (
            self.engine.dialect.name == "sqlite"
            and self.engine.url.database not in (None, "", ":memory:")
            and not Path(self.engine.url.database or "").exists()
        )
        new, identical = 0, 0
        if dry_run and missing_database:
            new = len(export.cases)
        else:
            with Session(self.engine) as db, db.begin():
                pending = []
                for case in export.cases:
                    row = db.get(DraftCaseRow, (case.case_id, case.version))
                    if row:
                        if row.content_hash != materials[case.case_id]:
                            raise InvalidStateError(
                                "Conflit de brouillon: nouvelle version requise"
                            )
                        self._verified_material(db, row)
                        identical += 1
                    else:
                        new += 1
                        pending.append(case)
                if not dry_run and pending:
                    batch = db.get(DraftBatchRow, export.content_hash)
                    if batch is None:
                        db.add(
                            DraftBatchRow(
                                id=export.content_hash,
                                payload={
                                    "export": export.model_dump(mode="json"),
                                    "receipt": receipt,
                                    "imported_at": datetime.now(UTC).isoformat(),
                                    "mapping_version": MAPPING_VERSION,
                                },
                            )
                        )
                        db.flush()
                    for case in pending:
                        db.add(
                            DraftCaseRow(
                                id=case.case_id,
                                version=case.version,
                                content_hash=materials[case.case_id],
                                batch_id=export.content_hash,
                                status="draft_unvalidated",
                            )
                        )
        return {
            "cas_nouveaux": new,
            "cas_identiques": identical,
            "cas_importes_cette_operation": 0 if dry_run else new,
            "zone": "clinical_draft_cases — privée, non exécutable",
            "ecriture": not dry_run and new > 0,
            "reimport": "identique: reçu initial conservé, aucune approbation créée",
        }

    def _verified_material(self, db: Session, row: DraftCaseRow) -> dict[str, Any]:
        batch = db.get(DraftBatchRow, row.batch_id)
        if batch is None:
            raise InvalidStateError("Lot source manquant")
        export = FreiburgExport.model_validate_json(json.dumps(batch.payload["export"]))
        if export.content_hash != batch.id or batch.payload["mapping_version"] != MAPPING_VERSION:
            raise InvalidStateError("Intégrité du lot invalide")
        case = next(
            (c for c in export.cases if (c.case_id, c.version) == (row.id, row.version)), None
        )
        if case is None:
            raise InvalidStateError("Cas absent du lot référencé")
        material = review_material(export, case)
        if row.content_hash != material_hash(material) or row.status != "draft_unvalidated":
            raise InvalidStateError("Intégrité du brouillon invalide")
        return {
            "id": row.id,
            "version": row.version,
            "status": row.status,
            "hash": row.content_hash,
            "material": material,
            "receipt": batch.payload["receipt"],
            "imported_at": batch.payload["imported_at"],
            "blockers": case_blockers(export, case),
            "reviews": [],
            "eligible": False,
        }

    def inspect(self, case_id: str | None = None, version: str | None = None) -> dict[str, Any]:
        with Session(self.engine) as db:
            query = select(DraftCaseRow).order_by(DraftCaseRow.id, DraftCaseRow.version)
            if case_id is not None:
                query = query.where(DraftCaseRow.id == case_id, DraftCaseRow.version == version)
            rows = list(db.scalars(query))
            if case_id is not None and not rows:
                raise NotFoundError("Brouillon inconnu")
            return {
                "zone": "brouillons privés non exécutables",
                "cas_importes": len(rows),
                "cas": [self._verified_material(db, r) for r in rows],
                "revues_cliniques": 0,
                "revues_linguistiques": 0,
                "droits_confirmes": 0,
                "publies": 0,
            }
