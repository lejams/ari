from __future__ import annotations

import argparse
from pathlib import Path

from ari.benchmarks.runner import (
    run_offline_benchmark,
    validate_live_guard,
    write_reports,
)
from ari.benchmarks.schemas import load_rules, load_suite

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RULES = PROJECT_ROOT / "benchmarks" / "voice" / "champion_challenger.v1.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark vocal ARI, hors ligne par défaut.")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--stack", action="append", dest="stacks")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--validate-live-guard-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_live_guard(
        mode=args.mode,
        maximum_cost_usd=args.max_cost_usd,
        confirmed=args.confirm_live,
    )
    if args.validate_live_guard_only:
        if args.mode != "live":
            raise ValueError("--validate-live-guard-only requires --mode live")
        print("Garde-fous live validés ; aucun appel fournisseur exécuté.")
        return 0
    if args.mode == "live":
        raise RuntimeError(
            "Live provider execution is intentionally unavailable until a separately "
            "authorized run supplies a reviewed adapter."
        )
    suite = load_suite(args.suite)
    rules = load_rules(args.rules)
    stacks = tuple(args.stacks or sorted(suite.offline_stack_fixtures))
    report = run_offline_benchmark(
        suite=suite,
        rules=rules,
        stack_ids=stacks,
        repetitions=args.repetitions,
        seed=args.seed,
        scenario_filters=tuple(args.scenario),
    )
    paths = write_reports(
        report,
        output_directory=args.output_dir,
        suite_path=args.suite,
        rules_path=args.rules,
        project_root=PROJECT_ROOT,
    )
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
