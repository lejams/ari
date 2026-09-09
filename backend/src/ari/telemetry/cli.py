from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ari.application.services.telemetry import aggregate_voice_metrics
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include a UTC offset")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Agrège les métriques vocales ARI sans exposer de transcript."
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--window-start", type=_aware_datetime)
    parser.add_argument("--window-end", type=_aware_datetime)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = SqliteSessionRepository(args.database_url)
    report = aggregate_voice_metrics(
        repository.list_voice_turn_metrics(),
        window_start=args.window_start,
        window_end=args.window_end,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
