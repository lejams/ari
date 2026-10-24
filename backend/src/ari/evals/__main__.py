"""Command line: `python -m ari.evals run|voice|compare|fingerprint|promotion ...`.

See docs/EVALS.md. Real models are only called with `--live` and a configuration whose
provider is not `fake`; every call is then costed with the versioned price list.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from ari.config import PROJECT_ROOT, Settings
from ari.evals import gates as gate_rules
from ari.evals.compare import CompareError, compare, render_markdown
from ari.evals.configs import ConfigError, build_provider, load_config, load_prompts
from ari.evals.manifest import DatasetError, load_dataset, parse_suite_ref
from ari.evals.promotion import PROMOTION_RUNS, fingerprint, verdict
from ari.evals.report import Report, format_micro_eur, read_report, write_report
from ari.evals.runner import RunRequest, run
from ari.evals.suites import SUITES
from ari.infrastructure.pricing import load_pricing

EVALS_ROOT = PROJECT_ROOT / "evals"
DEFAULT_CONFIGS = EVALS_ROOT / "configs"
DEFAULT_DATASETS = EVALS_ROOT / "datasets"
DEFAULT_GATES = EVALS_ROOT / "gates.yaml"
DEFAULT_PROMPTS = PROJECT_ROOT / "backend" / "src" / "ari" / "prompts"
# The price list of the code (not of the local .env), so a report never depends on the machine.
DEFAULT_PRICING: Path = Settings.model_fields["pricing_path"].default
DEFAULT_REPORTS = PROJECT_ROOT / "var" / "evals" / "reports"
DEFAULT_ADMIN_DATABASE = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ari.evals", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run_cmd = commands.add_parser("run", help="exécuter une suite sur une configuration")
    run_cmd.add_argument("--suite", required=True, help="<nom>@<version>, ex. patient-leakage@1")
    run_cmd.add_argument("--config", required=True, type=Path, help="fichier YAML de config")
    run_cmd.add_argument("--split", choices=("dev", "holdout"), default="dev")
    run_cmd.add_argument("--gate", action="store_true", help="mode gate : seuils, holdout autorisé")
    run_cmd.add_argument("--repeat", type=int, help="répétitions par item (gate : gates.yaml)")
    run_cmd.add_argument("--live", action="store_true", help="autoriser les vrais modèles")
    run_cmd.add_argument("--baseline", type=Path, help="rapport de référence (erreurs critiques)")
    run_cmd.add_argument("--out", type=Path, help="chemin du rapport JSON")
    run_cmd.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    run_cmd.add_argument("--gates", type=Path, default=DEFAULT_GATES)
    run_cmd.add_argument("--prompts-dir", type=Path, default=DEFAULT_PROMPTS)
    run_cmd.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    run_cmd.add_argument("--concurrency", type=int, default=4)

    voice_cmd = commands.add_parser(
        "voice", help="apprenant simulé voice-sim sur la chaîne vocale (en processus)"
    )
    voice_cmd.add_argument("--suite", default="voice-sim@1")
    voice_cmd.add_argument("--config", required=True, type=Path)
    voice_cmd.add_argument("--gate", action="store_true", help="appliquer les seuils")
    voice_cmd.add_argument("--repeat", type=int, help="sessions par script et par mode")
    voice_cmd.add_argument("--baseline", type=Path)
    voice_cmd.add_argument("--out", type=Path)
    voice_cmd.add_argument(
        "--admin-database-url",
        default=os.environ.get("ARI_TEST_DATABASE_URL", DEFAULT_ADMIN_DATABASE),
        help="base de maintenance où créer puis supprimer une base jetable",
    )
    voice_cmd.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    voice_cmd.add_argument("--gates", type=Path, default=DEFAULT_GATES)
    voice_cmd.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)

    compare_cmd = commands.add_parser(
        "compare", help="comparer des rapports ; le premier est la baseline"
    )
    compare_cmd.add_argument("reports", nargs="+", type=Path)
    compare_cmd.add_argument("--gates", type=Path, default=DEFAULT_GATES)
    compare_cmd.add_argument("--out", type=Path, help="chemin de la comparaison JSON")

    fingerprint_cmd = commands.add_parser(
        "fingerprint", help="empreinte IA d'un checkout (prompts, modèles, routage)"
    )
    fingerprint_cmd.add_argument("--root", type=Path, default=PROJECT_ROOT)
    fingerprint_cmd.add_argument("--out", type=Path, help="chemin de l'empreinte JSON")

    promotion_cmd = commands.add_parser(
        "promotion", help="les runs exigés pour une mise en production, et leur verdict"
    )
    promotion_cmd.add_argument(
        "--split",
        choices=("dev", "holdout"),
        default="holdout",
        help="holdout : gate de promotion ; dev : mesure seule, jamais un verdict passé",
    )
    promotion_cmd.add_argument("--live", action="store_true", help="autoriser les vrais modèles")
    promotion_cmd.add_argument("--out-dir", required=True, type=Path)
    promotion_cmd.add_argument("--configs-dir", type=Path, default=DEFAULT_CONFIGS)
    return parser


def _summary(report: Report, path: Path) -> str:
    lines = [
        f"{report.suite} ({report.split}), configuration `{report.config.id}` : "
        f"{report.dataset.items} items x {report.repetitions} répétition(s), "
        f"{report.duration_ms / 1000:.1f} s",
        f"Coût : total {format_micro_eur(report.cost.total_micro_eur)}, moyen par cas "
        f"{format_micro_eur(report.cost.mean_micro_eur_per_case)}, "
        f"{report.cost.unknown_calls}/{report.cost.calls} appel(s) au coût inconnu",
    ]
    for name, value in report.metrics.items():
        lines.append(f"  {name:<40} {'non mesuré' if value is None else f'{value:.4f}'}")
    if report.critical_errors:
        lines.append(f"Erreurs critiques : {len(report.critical_errors)}")
    if report.gate is not None:
        lines.append(f"Gate : {'PASSÉ' if report.gate.status == 'passed' else 'ÉCHOUÉ'}")
        for check in report.gate.checks:
            if check.status != "passed":
                lines.append(f"  {check.metric} {check.bound} : {check.status} ({check.value})")
        for error in report.gate.new_critical_errors:
            lines.append(f"  nouvelle erreur critique : {error}")
    lines.append(f"Rapport : {path}")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    name, version = parse_suite_ref(args.suite)
    suite = SUITES.get(name)
    if suite is None:
        raise DatasetError(f"suite inconnue {name!r} (connues : {', '.join(sorted(SUITES))})")
    if args.split == "holdout" and not args.gate:
        raise DatasetError("le holdout n'est lu qu'avec --gate")
    dataset = load_dataset(args.datasets_dir, name, version)
    config, config_hash = load_config(args.config)
    gates, _ = gate_rules.load_gates(args.gates)
    repetitions = args.repeat or (gates.repetitions if args.gate else 1)
    if args.gate and repetitions < gates.repetitions:
        raise ConfigError(f"un run gate répète chaque item {gates.repetitions} fois au moins")
    pricing = load_pricing(args.pricing)
    report = await run(
        RunRequest(
            suite=suite,
            dataset=dataset,
            split=args.split,
            gate_mode=args.gate,
            config=config,
            config_sha256=config_hash,
            provider=build_provider(config, live=args.live, pricing=pricing),
            prompts=load_prompts(config, suite.prompt_roles, args.prompts_dir),
            pricing=pricing,
            repetitions=repetitions,
            gates=gates,
            baseline=read_report(args.baseline) if args.baseline else None,
            concurrency=args.concurrency,
            code_version=os.environ.get("GITHUB_SHA"),
        )
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or DEFAULT_REPORTS / dataset.ref / f"{stamp}-{config.id}-{args.split}.json"
    write_report(report, out)
    print(_summary(report, out))
    return 1 if report.gate is not None and report.gate.status == "failed" else 0


def _voice(args: argparse.Namespace) -> int:
    from ari.evals.voice.runner import (
        VoiceRunRequest,
        load_voice_config,
        run_inprocess,
        throwaway_database,
    )
    from ari.evals.voice.script import load_voice_dataset

    name, version = parse_suite_ref(args.suite)
    if name != "voice-sim":
        raise DatasetError(f"la commande voice exécute voice-sim, pas {name}")
    dataset = load_voice_dataset(args.datasets_dir, version)
    config, config_hash = load_voice_config(args.config)
    gates, _ = gate_rules.load_gates(args.gates)
    repetitions = args.repeat or (gates.repetitions if args.gate else 1)
    if args.gate and repetitions < gates.repetitions:
        raise ConfigError(f"un run gate répète chaque script {gates.repetitions} fois au moins")
    with throwaway_database(args.admin_database_url) as database_url:
        report = run_inprocess(
            VoiceRunRequest(
                dataset=dataset,
                config=config,
                config_sha256=config_hash,
                database_url=database_url,
                pricing=load_pricing(args.pricing),
                pricing_path=args.pricing,
                repetitions=repetitions,
                gates=gates,
                gate_mode=args.gate,
                baseline=read_report(args.baseline) if args.baseline else None,
                code_version=os.environ.get("GITHUB_SHA"),
            )
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or DEFAULT_REPORTS / dataset.ref / f"{stamp}-{config.id}.json"
    write_report(report, out)
    print(_summary(report, out))
    return 1 if report.gate is not None and report.gate.status == "failed" else 0


def _compare(args: argparse.Namespace) -> int:
    gates, _ = gate_rules.load_gates(args.gates)
    comparison = compare([read_report(path) for path in args.reports], gates)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(comparison.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(render_markdown(comparison))
    return 0


def _fingerprint(args: argparse.Namespace) -> int:
    result = fingerprint(args.root)
    text = result.model_dump_json(indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


def _promotion(args: argparse.Namespace) -> int:
    """Every run of PROMOTION_RUNS, then `verdict.json`; a run that cannot start is missing."""
    gate = args.split == "holdout"
    reports: list[Report] = []
    config_ids: dict[str, str] = {}
    for promotion_run in PROMOTION_RUNS:
        config_path = args.configs_dir / promotion_run.config
        config_ids[promotion_run.config] = load_config(config_path)[0].id
        out = args.out_dir / f"{promotion_run.suite}--{config_path.stem}.json"
        argv = ["run", "--suite", promotion_run.suite, "--config", str(config_path)]
        argv += ["--split", args.split, "--out", str(out)]
        argv += ["--gate"] if gate else ["--repeat", "3"]
        argv += ["--live"] if args.live else []
        try:
            asyncio.run(_run(_parser().parse_args(argv)))
        except (DatasetError, ConfigError) as exc:
            print(
                f"Erreur ({promotion_run.suite}, {promotion_run.config}) : {exc}", file=sys.stderr
            )
            continue
        reports.append(read_report(out))
    result = verdict(
        reports,
        config_ids,
        fingerprint(PROJECT_ROOT).sha256,
        code=os.environ.get("GITHUB_SHA"),
        measurement=not gate,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "verdict.json").write_text(result.model_dump_json(indent=2) + "\n", "utf-8")
    print(f"Verdict de promotion : {result.status}")
    for run_verdict in result.runs:
        reason = f" ({run_verdict.reason})" if run_verdict.reason else ""
        print(f"  {run_verdict.suite} {run_verdict.config} : {run_verdict.status}{reason}")
    return 1 if result.status == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return asyncio.run(_run(args))
        if args.command == "voice":
            return _voice(args)
        if args.command == "fingerprint":
            return _fingerprint(args)
        if args.command == "promotion":
            return _promotion(args)
        return _compare(args)
    except (DatasetError, ConfigError, CompareError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
