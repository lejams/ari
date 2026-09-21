"""Operator CLI of the content pipeline (`python -m ari.content.cli`).

Ingests PDFs with their declaration, drives the queue, lists and shows protocols. It prints
JSON; anything derived from protocol text goes through the contact redaction first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ari.config import Settings
from ari.content.container import ContentContainer, build_content_container
from ari.content.domain.documents import Actor, DocumentDeclaration, JobStatus
from ari.content.domain.protocol import ProtocolStatus
from ari.domain.clinical_privacy import redact_contacts
from ari.domain.errors import AriError
from ari.domain.geography import Land
from ari.worker import default_worker_id, run_one

CLI_ACTOR = Actor(kind="cli", name="cli-operator", account_id="cli-operator")


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _print(value: Any) -> None:
    print(json.dumps(redact_contacts(_jsonable(value)), ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pipeline de contenu ARI : PDF → protocoles → gold"
    )
    parser.add_argument("--database-url", help="Base content ; défaut ARI_CONTENT_DATABASE_URL")
    parser.add_argument(
        "--storage-dir", type=Path, help="Répertoire des PDF ; défaut ARI_CONTENT_STORAGE_DIR"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "ingest", help="Déposer un PDF avec sa déclaration et lancer l'extraction"
    )
    ingest.add_argument("pdf", type=Path)
    ingest.add_argument("--land", choices=[land.value for land in Land])
    ingest.add_argument("--city")
    ingest.add_argument("--exam-body")
    ingest.add_argument("--exam-date", help="AAAA-MM")
    ingest.add_argument("--specialty")
    ingest.add_argument("--provenance", required=True)
    ingest.add_argument(
        "--rights", choices=["unknown", "incompatible", "compatible"], default="unknown"
    )
    ingest.add_argument("--rights-evidence")
    ingest.add_argument("--intended-use", default="Entraînement FSP dans ARI")
    ingest.add_argument(
        "--consent", required=True, help="Qui a fourni le fichier et ce qu'il a accepté"
    )

    jobs = sub.add_parser("run-jobs", help="Exécuter les tâches en attente")
    jobs.add_argument("--once", action="store_true", help="Vider la file puis s'arrêter")
    listing = sub.add_parser("jobs")
    listing.add_argument("--status", choices=[s.value for s in JobStatus])
    retry = sub.add_parser("retry-job")
    retry.add_argument("job_id")

    sub.add_parser("list-documents")
    protocols = sub.add_parser("list-protocols")
    protocols.add_argument("--document")
    protocols.add_argument("--status", choices=[s.value for s in ProtocolStatus])
    protocols.add_argument("--land", choices=[land.value for land in Land])
    show = sub.add_parser("show-protocol")
    show.add_argument("protocol_id")
    show.add_argument("--version", type=int)
    export = sub.add_parser("export-protocol")
    export.add_argument("protocol_id")
    export.add_argument("--out", type=Path, required=True)
    release = sub.add_parser(
        "release", help="Envoyer un protocole (ou tout un document) en relecture"
    )
    release.add_argument("protocol_id", nargs="?")
    release.add_argument("--document")
    runs = sub.add_parser("ai-runs")
    runs.add_argument("--document")
    return parser


def _container(args: argparse.Namespace) -> ContentContainer:
    overrides: dict[str, Any] = {}
    if args.database_url:
        overrides["content_database_url"] = args.database_url
    if args.storage_dir:
        overrides["content_storage_dir"] = args.storage_dir
    return build_content_container(Settings(**overrides))


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except ValidationError as exc:
        print(
            json.dumps(
                {"erreur": "validation", "details": exc.errors(include_input=False)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
    except AriError as exc:
        print(
            json.dumps({"erreur": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
    except (OSError, ValueError, NotImplementedError) as exc:
        print(
            json.dumps({"erreur": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
    return 2


def _dispatch(args: argparse.Namespace) -> int:
    container = _container(args)
    if args.command == "ingest":
        declaration = DocumentDeclaration(
            provenance=args.provenance,
            intended_use=args.intended_use,
            consent_declaration=args.consent,
            rights=args.rights,
            rights_evidence=args.rights_evidence,
            land=Land(args.land) if args.land else None,
            city=args.city,
            exam_body=args.exam_body,
            exam_date=args.exam_date,
            specialty=args.specialty,
        )
        result = container.ingestion.ingest(
            args.pdf.read_bytes(), args.pdf.name, declaration, actor=CLI_ACTOR, via="cli"
        )
        _print({"document": result.document, "duplicate": result.duplicate})
    elif args.command == "run-jobs":
        processed = asyncio.run(_run_jobs(container, once=args.once))
        _print({"processed": processed})
    elif args.command == "jobs":
        _print(container.queue.list(JobStatus(args.status) if args.status else None))
    elif args.command == "retry-job":
        _print(container.queue.retry(args.job_id))
    elif args.command == "list-documents":
        with container.repository.transaction() as tx:
            _print(tx.list_documents())
    elif args.command == "list-protocols":
        with container.repository.transaction() as tx:
            heads = tx.list_heads(
                status=ProtocolStatus(args.status) if args.status else None,
                document_id=args.document,
                land=Land(args.land) if args.land else None,
            )
            _print(
                [
                    {
                        "id": h.id,
                        "version": h.version,
                        "status": h.status,
                        "document_id": h.document_id,
                        "pages": [h.page_from, h.page_to],
                        "land": h.record.location.land,
                        "open_blockers": h.record.review_blockers("doctor"),
                        "pii_findings": len(h.pii_findings),
                    }
                    for h in heads
                ]
            )
    elif args.command in ("show-protocol", "export-protocol"):
        with container.repository.transaction() as tx:
            protocol = (
                tx.get_protocol(args.protocol_id, args.version)
                if args.command == "show-protocol" and args.version
                else tx.head(args.protocol_id)
            )
            if protocol is None:
                raise AriError("Protocole inconnu")
            view = {
                "protocol": protocol,
                "reviews": tx.reviews(protocol.id),
                "events": tx.events(protocol.id),
                "blockers": {
                    "doctor": protocol.record.review_blockers("doctor"),
                    "owner": protocol.record.review_blockers("owner"),
                },
            }
        if args.command == "export-protocol":
            args.out.write_text(
                json.dumps(_jsonable(view), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _print({"written": str(args.out)})
        else:
            _print(view)
    elif args.command == "release":
        if args.document:
            _print(
                {"released": container.workflow.release_document(args.document, actor=CLI_ACTOR)}
            )
        elif args.protocol_id:
            _print(container.workflow.release(args.protocol_id, actor=CLI_ACTOR))
        else:
            raise ValueError("Indiquez un protocole ou --document")
    elif args.command == "ai-runs":
        with container.repository.transaction() as tx:
            _print(tx.ai_runs(document_id=args.document))
    return 0


async def _run_jobs(container: ContentContainer, *, once: bool) -> int:
    worker_id = default_worker_id(container.settings)
    processed = 0
    while True:
        job = await run_one(container, worker_id)
        if job is None:
            if once:
                return processed
            await asyncio.sleep(container.settings.worker_poll_seconds)
            continue
        processed += 1


if __name__ == "__main__":
    raise SystemExit(run())
