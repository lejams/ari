"""Adapter for the documented provisional Work contract, not an invented Freiburg format."""

import hashlib
import json
from pathlib import Path
from typing import Any

from ari.domain.clinical import (
    ClinicalBundle,
    ClinicalCaseVersion,
    RubricVersion,
    TerminologySetVersion,
)
from ari.domain.clinical_privacy import contact_locations, redact_contacts
from ari.infrastructure.cases.clinical_store import decode
from ari.infrastructure.cases.yaml_io import read_yaml


class WorkFormatError(ValueError):
    """Actionable format errors containing no untrusted file contents."""


def adapt_work_export(path: Path) -> tuple[ClinicalBundle, dict[str, Any]]:
    if path.suffix.lower() == ".xlsx":
        raise WorkFormatError("XLSX réservé à la revue humaine ; fournir l'export JSON machine.")
    data_bytes = path.read_bytes()
    raw = read_yaml(data_bytes.decode("utf-8"))
    if isinstance(raw, dict) and raw.get("schema_version") == "ari-clinical-cases-draft-0.1":
        raise WorkFormatError(
            "Export Freiburg 0.1: utiliser import-freiburg JSON --workbook XLSX; "
            "aucun scénario exécutable ne peut être déduit des champs manquants."
        )
    if not isinstance(raw, dict) or raw.get("export_contract") != "work-ari-draft-v1":
        raise WorkFormatError("Format Work non reconnu; contrat work-ari-draft-v1 attendu")
    allowed = {
        "export_contract",
        "training_bundle",
        "community_comments",
        "testimony_authors",
        "contacts",
    }
    if set(raw) - allowed:
        raise ValueError("Champs Work inconnus: adapter le format réel avant d'importer")
    payload = raw.get("training_bundle")
    if not isinstance(payload, dict):
        raise ValueError("training_bundle doit être un objet")
    # Only these structured fields reach training. Raw narratives/author metadata stay private.
    if set(payload) - {
        "schema_version",
        "sources",
        "rubrics",
        "terminology_sets",
        "cases",
        "scenarios",
    }:
        raise ValueError("Champs de bundle inconnus, notamment tout statut importé")
    for section in ("sources", "cases", "scenarios", "rubrics", "terminology_sets"):
        entries = payload.get(section)
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise WorkFormatError(f"{section} doit être une liste d'objets structurés.")
    locations: list[str] = []
    for section in ("cases", "scenarios", "rubrics", "terminology_sets"):
        locations.extend(contact_locations(payload.get(section), section))
        payload[section] = redact_contacts(payload.get(section))
    checksum = hashlib.sha256(data_bytes).hexdigest()
    for source in payload.get("sources", []):
        if not isinstance(source, dict):
            raise ValueError("Source Work ambiguë")
        source.update(
            {
                "source_type": "work_export",
                "document_reference": path.name,
                "immediate_source_checksum": checksum,
                "private_material_reference": str(path.resolve()),
                "original_verified": False,
            }
        )
    if locations:
        for case in payload.get("cases", []):
            questions = case.setdefault("unresolved_questions", [])
            if not isinstance(questions, list):
                raise WorkFormatError("unresolved_questions doit être une liste structurée.")
            questions.append(
                {
                    "id": "work-contact-redaction",
                    "critical": True,
                    "text": "Coordonnées retirées automatiquement. Relecture, correction "
                    "des formulations et nouvelle version nécessaires.",
                }
            )
    # Work hashes are transport metadata, never a publication/review authority.
    cases = {
        (c["id"], c["version"]): decode(ClinicalCaseVersion, c) for c in payload.get("cases", [])
    }
    rubrics = {
        (r["id"], r["version"]): decode(RubricVersion, r) for r in payload.get("rubrics", [])
    }
    terms = {
        (t["id"], t["version"]): decode(TerminologySetVersion, t)
        for t in payload.get("terminology_sets", [])
    }
    try:
        for scenario in payload.get("scenarios", []):
            for field, resources in (("case", cases), ("rubric", rubrics), ("terminology", terms)):
                ref = scenario[field]
                scenario[f"{field}_hash"] = resources[(ref["id"], ref["version"])].content_hash
    except (KeyError, TypeError) as exc:
        raise ValueError("Référence Work absente ou ambiguë") from exc
    bundle = ClinicalBundle.model_validate_json(json.dumps(payload, allow_nan=False))
    return bundle, {
        "contrat": "work-ari-draft-v1 (bundle ARI préparé, distinct des exports Freiburg 0.1)",
        "checksum_export": checksum,
        "champs_contacts_nettoyes": len(locations),
        "metadonnees_exclues": sorted(
            set(raw) & {"community_comments", "testimony_authors", "contacts"}
        ),
        "anonymisation_garantie": False,
        "avertissement": "Les noms/récits identifiants exigent toujours une revue humaine.",
    }
