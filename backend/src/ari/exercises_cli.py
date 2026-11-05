"""Command line of the bank of language exercises: generate, import, list, withdraw.

    python -m ari.exercises_cli generate --config evals/configs/exercises/luna-haiku.yaml \\
        --live --spec B1:case:cloze:10 --out var/exercises/batch.json
    python -m ari.exercises_cli import var/exercises/batch.json \\
        --gate-report exercise-gate/report.json

`generate` calls the models (billed, `--live` to confirm) and writes a batch file; it never
touches the database. A person can read the batch. `import` is the only way into the bank: it
re-runs every check the code can run, refuses a batch that no passing gate report backs, and
writes the exercises with the checks they went through. Local tool of the owner, like the
clinical registry CLI (`ari.infrastructure.cases.cli`): no learner request reaches it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ari.application.exercise_batch import ExerciseBatch
from ari.application.exercise_schemas import RUBRIC_VERSION, BlindSolveSchema, JudgeSchema
from ari.application.ports.exercises import ExerciseBank
from ari.application.services.exercise_bank import (
    CheckOutcome,
    ExerciseAuthor,
    ExerciseChecker,
    ExerciseFactory,
    ExerciseSpec,
    reviews_of,
    solver_accepts,
)
from ari.config import PROJECT_ROOT, Settings
from ari.domain.errors import AriError, InvalidStateError, NotFoundError
from ari.domain.language_exercises import (
    EXERCISE_CATEGORIES,
    EXERCISE_FORMATS,
    EXERCISE_LEVELS,
    MAX_LEVEL_RELABEL,
    MECHANICAL_CHECKS_VERSION,
    ExerciseCandidate,
    LanguageExercise,
    level_gap,
)
from ari.domain.models import new_id, utc_now
from ari.evals.exercises.gate import require_passing_gate
from ari.evals.report import read_report
from ari.infrastructure.exercises import models as exercise_models
from ari.infrastructure.persistence.platform.engine import create_platform_engine
from ari.infrastructure.persistence.platform.exercises import SqlExerciseBank

DEFAULT_CONFIG = PROJECT_ROOT / "evals" / "configs" / "exercises" / "luna-sonnet.yaml"
MAX_PER_SPEC = 40
CALLS_PER_REQUEST = 5  # Exercises asked of the author in one call: fewer repeats, shorter output.


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Banque d'exercices de langue ARI — outil local")
    parser.add_argument("--database-url", help="Base de la plateforme ; aucune migration auto")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser(
        "generate", help="Écrire un lot (appelle les modèles, ne touche pas la base)"
    )
    generate.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    generate.add_argument("--live", action="store_true", help="confirmer l'appel de vrais modèles")
    generate.add_argument(
        "--spec",
        action="append",
        required=True,
        metavar="NIVEAU:CATEGORIE:FORMAT:N",
        help="ex. B1:case:cloze:10 (répétable)",
    )
    generate.add_argument("--setting", help="décor souhaité (indication à l'auteur)")
    generate.add_argument("--out", type=Path, required=True)
    generate.add_argument("--no-bank", action="store_true", help="ne pas lire la banque (doublons)")
    importer = sub.add_parser("import", help="Importer un lot dans la banque")
    importer.add_argument("batch", type=Path)
    importer.add_argument(
        "--gate-report", type=Path, required=True, help="rapport de gate passé (evals-exercises)"
    )
    importer.add_argument("--dry-run", action="store_true")
    listing = sub.add_parser("list", help="Lister les exercices actifs")
    listing.add_argument("--level", choices=EXERCISE_LEVELS)
    listing.add_argument("--category", choices=EXERCISE_CATEGORIES)
    withdraw = sub.add_parser("withdraw", help="Retirer un exercice (définitif)")
    withdraw.add_argument("exercise_id")
    withdraw.add_argument("--reason", required=True)
    withdraw.add_argument("--actor", required=True, help="Identité humaine déclarée")
    return parser


def parse_spec(text: str) -> tuple[ExerciseSpec, int]:
    parts = text.split(":")
    if len(parts) != 4:
        raise ValueError(f"spec attendue sous la forme NIVEAU:CATEGORIE:FORMAT:N, reçu {text!r}")
    level, category, fmt, count = parts
    if level not in EXERCISE_LEVELS or category not in EXERCISE_CATEGORIES:
        raise ValueError(f"niveau ou catégorie inconnus dans {text!r}")
    if fmt not in EXERCISE_FORMATS or not count.isdigit() or not 1 <= int(count) <= MAX_PER_SPEC:
        raise ValueError(f"format inconnu ou N hors de 1..{MAX_PER_SPEC} dans {text!r}")
    return ExerciseSpec(level, category, fmt), int(count)


def _check_against_bank(exercises: list[LanguageExercise], bank: ExerciseBank) -> int:
    """How many exercises of the batch are already in the bank; raises on a clash.

    A clash is a correct sentence that the bank holds under another exercise, or that the batch
    holds twice: the write would refuse it, after the exercises before it were already written.
    """
    taken = bank.dedup_keys()
    seen: set[str] = set()
    present = 0
    for exercise in exercises:
        key = exercise.dedup_key()
        if key in seen:
            raise InvalidStateError(
                f"Lot refusé : la phrase correcte de {exercise.exercise_id} "
                "est deux fois dans le lot"
            )
        seen.add(key)
        if key not in taken:
            continue
        try:
            bank.get(exercise.exercise_id)
        except NotFoundError:
            raise InvalidStateError(
                f"Lot refusé : la phrase correcte de {exercise.exercise_id} est déjà dans la "
                "banque sous un autre exercice"
            ) from None
        present += 1
    return present


def import_batch(
    batch: ExerciseBatch, bank: ExerciseBank, *, gate_report_path: Path, dry_run: bool = False
) -> dict[str, Any]:
    """Everything the code can verify again, then the write. Raises on the first doubt."""
    for stage in (batch.author, batch.solver, batch.judge):
        if stage.provider in ("fake", "none"):
            raise InvalidStateError(
                f"Lot refusé : l'étape {stage.stage} n'a pas tourné sur un vrai modèle"
            )
    if (batch.solver.provider, batch.solver.model) == (batch.author.provider, batch.author.model):
        raise InvalidStateError("Lot refusé : le contrôle et l'auteur sont le même modèle")
    if (batch.judge.provider, batch.judge.model) == (batch.author.provider, batch.author.model):
        raise InvalidStateError("Lot refusé : le juge et l'auteur sont le même modèle")
    if (batch.rubric_version, batch.mechanical_checks_version) != (
        RUBRIC_VERSION,
        MECHANICAL_CHECKS_VERSION,
    ):
        raise InvalidStateError(
            f"Lot refusé : fait avec {batch.rubric_version} et {batch.mechanical_checks_version}, "
            f"ce code applique {RUBRIC_VERSION} et {MECHANICAL_CHECKS_VERSION}"
        )
    gate_report = read_report(gate_report_path)
    require_passing_gate(gate_report, batch)
    report: dict[str, Any] = {"lot": batch.batch_id, "nouveaux": 0, "deja_presents": 0}
    prepared = []
    for entry in batch.accepted:
        candidate = ExerciseCandidate.model_validate_json(json.dumps(entry.exercise))
        exercise = LanguageExercise.model_validate_json(candidate.model_dump_json())
        if exercise.exercise_id != entry.exercise_id:
            raise InvalidStateError(
                f"Lot refusé : l'identifiant de {entry.exercise_id} ne correspond pas au contenu"
            )
        solved = BlindSolveSchema.model_validate_json(json.dumps(entry.solver))
        judged = JudgeSchema.model_validate_json(json.dumps(entry.judge))
        if not solver_accepts(exercise, solved) or not judged.approved:
            raise InvalidStateError(
                f"Lot refusé : les verdicts de {entry.exercise_id} ne l'approuvent pas"
            )
        # The stored level is the judge's estimate; the declared one is at most one step away.
        declared = entry.level_relabeled_from or exercise.level
        if exercise.level != judged.estimated_level or (
            level_gap(declared, exercise.level) > MAX_LEVEL_RELABEL
        ):
            raise InvalidStateError(
                f"Lot refusé : le niveau de {entry.exercise_id} ne suit pas l'estimation du juge"
            )
        prepared.append((exercise, solved, judged))
    # Refuse the whole batch before writing anything when one sentence is already taken by
    # another exercise, so that a refusal never leaves half a batch in the bank.
    present = _check_against_bank([exercise for exercise, _, _ in prepared], bank)
    if dry_run:
        report["a_importer"] = len(prepared) - present
        report["deja_presents"] = present
        return report
    provenance = {
        "batch_id": batch.batch_id,
        "author": batch.author.model_dump(mode="json"),
        "solver": batch.solver.model_dump(mode="json"),
        "judge": batch.judge.model_dump(mode="json"),
        "rubric_version": batch.rubric_version,
        "mechanical_checks_version": batch.mechanical_checks_version,
        "gate_report": {
            "suite": gate_report.suite,
            "config": gate_report.config.id,
            "started_at": gate_report.started_at.isoformat(),
        },
        "imported_at": utc_now().isoformat(),
    }
    added = bank.add_many(
        [
            (
                exercise,
                reviews_of(
                    CheckOutcome(True, "accepted", (), solved, judged), batch.solver, batch.judge
                ),
            )
            for exercise, solved, judged in prepared
        ],
        batch_id=batch.batch_id,
        provenance=provenance,
    )
    report["nouveaux"] = sum(added)
    report["deja_presents"] = len(added) - sum(added)
    return report


async def generate(args: argparse.Namespace) -> dict[str, Any]:
    config, config_sha256 = exercise_models.load_config(args.config)
    prompts = exercise_models.load_prompts(config)
    provider = exercise_models.build_provider(config, live=args.live)
    requests = [parse_spec(text) for text in args.spec]
    requests = [
        (ExerciseSpec(spec.level, spec.category, spec.format, args.setting), count)
        for spec, count in requests
    ]
    known = frozenset[str]()
    avoid: dict[tuple[str, str], list[str]] = {}
    if not args.no_bank:
        bank = SqlExerciseBank(create_platform_engine(args.database_url or Settings().database_url))
        known = bank.dedup_keys()
        for stored in bank.list_active():
            avoid.setdefault((stored.exercise.level, stored.exercise.category), []).append(
                stored.exercise.correct_sentence()
            )
    factory = ExerciseFactory(
        ExerciseAuthor(provider, prompts["author"]),
        ExerciseChecker(
            provider,
            solver_prompt=prompts["solver"],
            judge_prompt=prompts["judge"],
            author_model=config.author_identity,
            passes=config.check_passes,
        ),
        known_keys=known,
    )
    # One author call asks for at most CALLS_PER_REQUEST exercises: split the larger requests.
    split = [
        (spec, min(CALLS_PER_REQUEST, remaining))
        for spec, count in requests
        for remaining in _chunks(count)
    ]
    batch = await factory.run(f"batch-{utc_now():%Y%m%d%H%M%S}-{new_id()[:8]}", split, avoid=avoid)
    batch = batch.model_copy(update={"config_sha256": config_sha256})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(batch.model_dump_json(indent=2) + "\n", encoding="utf-8")
    rejected: dict[str, int] = {}
    for entry in batch.rejected:
        rejected[entry.stage] = rejected.get(entry.stage, 0) + 1
    return {
        "lot": batch.batch_id,
        "fichier": str(args.out),
        "acceptes": len(batch.accepted),
        "refuses": rejected,
        "appels": len(batch.calls),
        "auteur": f"{batch.author.provider}:{batch.author.model}",
        "controle": f"{batch.solver.provider}:{batch.solver.model}",
        "suite": "importer avec : import <fichier> --gate-report <rapport evals-exercises passé>",
    }


def _chunks(count: int) -> list[int]:
    return [CALLS_PER_REQUEST] * (count // CALLS_PER_REQUEST) + (
        [count % CALLS_PER_REQUEST] if count % CALLS_PER_REQUEST else []
    )


def _bank(args: argparse.Namespace) -> SqlExerciseBank:
    return SqlExerciseBank(create_platform_engine(args.database_url or Settings().database_url))


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            report = asyncio.run(generate(args))
        elif args.command == "import":
            batch = ExerciseBatch.model_validate_json(args.batch.read_text(encoding="utf-8"))
            report = import_batch(
                batch, _bank(args), gate_report_path=args.gate_report, dry_run=args.dry_run
            )
            report["ecriture"] = not args.dry_run
        elif args.command == "list":
            report = {
                "exercices": [
                    {
                        "id": s.exercise.exercise_id,
                        "niveau": s.exercise.level,
                        "categorie": s.exercise.category,
                        "format": s.exercise.format,
                        "phrase": s.exercise.correct_sentence(),
                    }
                    for s in _bank(args).list_active(level=args.level, category=args.category)
                ]
            }
        else:
            _bank(args).withdraw(args.exercise_id, reason=args.reason, actor=args.actor)
            report = {"retire": args.exercise_id, "motif": args.reason}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except ValidationError as exc:
        kind = exc.errors(include_input=False)[0]["type"]
        print(
            f"Schéma invalide : {exc.error_count()} erreur(s), première : {kind}", file=sys.stderr
        )
    except (AriError, ValueError, OSError) as exc:
        print(f"Opération refusée : {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(run())
