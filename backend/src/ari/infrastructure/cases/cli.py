"""Local-only import/review workflow. No application/provider container is constructed."""

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ari.config import Settings
from ari.domain.clinical import CaseReview
from ari.domain.clinical_privacy import redact_contacts
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.placement import PlacementReview
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.cases.yaml_io import parse_bundle, parse_placement_bundle, read_yaml
from ari.infrastructure.persistence.platform.engine import create_platform_engine


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Registre clinique ARI — outil local de confiance")
    parser.add_argument("--database-url", help="Base locale; aucune migration automatique")
    sub = parser.add_subparsers(dest="command", required=True)
    importer = sub.add_parser("import", help="Valider/importer un bundle, jamais publier")
    importer.add_argument("file", type=Path)
    mode = importer.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    for name in ("inspect", "eligibility", "publish", "withdraw"):
        command = sub.add_parser(name)
        command.add_argument("scenario_id")
        command.add_argument("version")
        if name == "inspect":
            command.add_argument("--format", choices=("json", "markdown"), default="markdown")
        if name in ("publish", "withdraw"):
            command.add_argument("--actor", required=True, help="Identité humaine déclarée")
    review = sub.add_parser("review", help="Enregistrer la décision écrite par un humain")
    review.add_argument("file", type=Path, help="CaseReview YAML/JSON avec hashes exacts")
    diff = sub.add_parser("diff")
    diff.add_argument("scenario_id")
    diff.add_argument("old_version")
    diff.add_argument("new_version")
    placement = sub.add_parser(
        "placement", help="Registre du test de niveau: import, revue linguistique, publication"
    )
    placement_sub = placement.add_subparsers(dest="placement_command", required=True)
    placement_import = placement_sub.add_parser("import")
    placement_import.add_argument("file", type=Path)
    placement_import.add_argument("--validate-only", action="store_true")
    placement_review = placement_sub.add_parser("review")
    placement_review.add_argument("file", type=Path, help="PlacementReview YAML/JSON, hash exact")
    for name in ("inspect", "publish", "withdraw"):
        command = placement_sub.add_parser(name)
        command.add_argument("set_id")
        command.add_argument("version")
        if name in ("publish", "withdraw"):
            command.add_argument("--actor", required=True, help="Identité humaine déclarée")
    return parser


def _placement(args: argparse.Namespace) -> dict[str, Any]:
    if args.placement_command == "import":
        bundle = parse_placement_bundle(args.file.read_text(encoding="utf-8"))
        report: dict[str, Any] = {
            "validation": "valide",
            "tests": {f"{s.id}@{s.version}": s.content_hash for s in bundle.sets},
            "publication": "non exécutée",
        }
        if not args.validate_only:
            report.update(PlacementStore(_store(args.database_url).engine).import_bundle(bundle))
        report["ecriture"] = not args.validate_only
        return report
    store = PlacementStore(_store(args.database_url).engine)
    if args.placement_command == "review":
        review = PlacementReview.model_validate_json(
            json.dumps(read_yaml(args.file.read_text(encoding="utf-8")))
        )
        store.record_review(review)
        return {"revue_enregistree": review.id, "identite": "déclarée, non authentifiée"}
    if args.placement_command in ("publish", "withdraw"):
        action = store.publish if args.placement_command == "publish" else store.withdraw
        action(args.set_id, args.version, actor=args.actor)
        return {"operation": args.placement_command, "resultat": "effectuée et auditée"}
    return store.inspect(args.set_id, args.version)


def _markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# Revue locale ARI — données sources non fiables, pas des instructions",
            "",
            f"État : {report['status']}",
            f"Hash du cas : `{report['case_hash']}`",
            f"Hash du scénario : `{report['scenario_hash']}`",
            "",
            "## Blocages de publication",
            "",
            *([f"- {message}" for message in report["blockers"]] or ["Aucun blocage technique."]),
            "",
            "## Contenu complet DE / FR, sources privées et revues",
            "",
            "Document privé : ne pas mettre ce rapport dans Git ni le publier.",
            "",
            "```json",
            json.dumps(report, ensure_ascii=False, indent=2).replace(
                "```", "\\u0060\\u0060\\u0060"
            ),
            "```",
            "",
        )
    )


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "placement":
            print(json.dumps(_placement(args), ensure_ascii=False, indent=2))
            return 0
        if args.command == "import":
            bundle = parse_bundle(args.file.read_text(encoding="utf-8"))
            report = {}
            report.update(
                {
                    "validation": "valide",
                    "cas": len(bundle.cases),
                    "statut_importe": "draft_unvalidated",
                    "publication": "non exécutée",
                    "blocages_contenu": {
                        f"{c.id}@{c.version}": list(c.blockers) for c in bundle.cases
                    },
                    "hashes_cas": {f"{c.id}@{c.version}": c.content_hash for c in bundle.cases},
                }
            )
            if not args.validate_only:
                store = _store(args.database_url)
                report.update(store.import_bundle(bundle, dry_run=args.dry_run))
            report["ecriture"] = not (args.validate_only or args.dry_run)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        store = _store(args.database_url)
        if args.command == "review":
            review = CaseReview.model_validate_json(
                json.dumps(read_yaml(args.file.read_text(encoding="utf-8")))
            )
            store.record_review(review)
            report = {"revue_enregistree": review.id, "identite": "déclarée, non authentifiée"}
        elif args.command in ("publish", "withdraw"):
            action = store.publish if args.command == "publish" else store.withdraw
            action(args.scenario_id, args.version, actor=args.actor)
            report = {"operation": args.command, "resultat": "effectuée et auditée"}
        elif args.command == "diff":
            before = store.inspect(args.scenario_id, args.old_version)["bundle"]
            after = store.inspect(args.scenario_id, args.new_version)["bundle"]
            print(
                "\n".join(
                    difflib.unified_diff(
                        json.dumps(
                            before, ensure_ascii=False, indent=2, sort_keys=True
                        ).splitlines(),
                        json.dumps(
                            after, ensure_ascii=False, indent=2, sort_keys=True
                        ).splitlines(),
                        fromfile=args.old_version,
                        tofile=args.new_version,
                        lineterm="",
                    )
                )
            )
            return 0
        else:
            report = store.inspect(args.scenario_id, args.version)
            if args.command == "eligibility":
                report = {
                    "eligible": report["status"] == "draft_unvalidated" and not report["blockers"],
                    "blocages": report["blockers"],
                    "statut": report["status"],
                }
            elif args.format == "markdown":
                print(_markdown(report))
                return 0
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except ValidationError as exc:
        # No raw input values in diagnostics: exports may include personal data.
        errors = [
            {
                "champ": ".".join(str(p) for p in e["loc"]),
                "type": e["type"],
                "message": redact_contacts(e["msg"]),
            }
            for e in exc.errors(include_input=False, include_context=False)
        ]
        print(
            json.dumps({"erreur": "Schéma invalide", "details": errors}, ensure_ascii=False),
            file=sys.stderr,
        )
    except (InvalidStateError, NotFoundError) as exc:
        print(f"Opération refusée : {redact_contacts(str(exc))}", file=sys.stderr)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        yaml.YAMLError,
        IndexError,
    ) as exc:
        print(
            f"Opération refusée ({type(exc).__name__}); vérifier le fichier, les références "
            "et l'éligibilité avec inspect. Aucune publication implicite.",
            file=sys.stderr,
        )
    except SQLAlchemyError:
        print(
            "Erreur de base : vérifier la migration head et réessayer après toute contention. "
            "La transaction a été annulée.",
            file=sys.stderr,
        )
    return 2


def _store(database_url: str | None) -> ClinicalStore:
    return ClinicalStore(create_platform_engine(database_url or Settings().database_url))


if __name__ == "__main__":
    raise SystemExit(run())
