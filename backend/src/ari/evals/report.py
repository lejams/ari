"""The JSON report of one run, and its summaries (cost, latency, metrics by segment).

A report says exactly what was measured: dataset version and hash, configuration and its
hash, prompt versions and hashes, schema hashes, scorer and price list versions, the model
recorded on every call, the cost of every call. A cost that cannot be computed stays None
and is counted apart; the mean cost per case is then None, never a partial sum.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ari.evals.gates import GateResult

REPORT_VERSION = "evals-report-v1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CallEntry(_Model):
    operation: str
    provider: str
    model: str  # Requested.
    model_returned: str | None  # What the provider says answered; None when unknown.
    status: str
    latency_ms: int
    cost_micro_eur: int | None
    unknown_components: list[str]
    usage: dict[str, Any]


class RunEntry(_Model):
    repetition: int
    attempts: int
    usable: bool
    verdict: bool
    outcomes: list[str]
    counts: dict[str, float]
    signature: str
    errors: list[str]
    calls: list[CallEntry]
    detail: dict[str, Any] | None  # None for the holdout: its content stays out of reports.


class ItemEntry(_Model):
    item_id: str
    family: str
    case_id: str
    verdict: bool  # Majority of the repetitions.
    outcomes: list[str]  # Every outcome seen in at least one repetition.
    counts: dict[str, float]  # Summed over repetitions, stability included.
    runs: list[RunEntry]


class CostSummary(_Model):
    pricing_version: str | None
    calls: int
    unknown_calls: int
    unknown_components: list[str]
    known_micro_eur: int
    total_micro_eur: int | None
    runs: int
    mean_micro_eur_per_case: int | None  # Per passage of one item; None if any cost unknown.
    per_operation: dict[str, dict[str, int | None]]


class LatencySummary(_Model):
    calls: int
    p50_ms: int | None
    p95_ms: int | None
    max_ms: int | None


class PromptVersion(_Model):
    version: str
    sha256: str


class Versions(_Model):
    prompts: dict[str, PromptVersion]
    output_schemas: dict[str, str]  # Schema name -> SHA-256 of its JSON schema.
    evaluation_schema_version: str | None
    scorer: str
    gates: str | None
    pricing: str | None
    code: str | None  # Commit, when the caller provides it (CI sets GITHUB_SHA).


class DatasetInfo(_Model):
    ref: str
    manifest_sha256: str
    synthetic: bool
    split: str
    items: int
    families: list[str]
    files: dict[str, str]
    # Case id -> "version:content_hash" of the published case actually played, when the
    # dataset does not hash its cases itself (voice-sim reads them from the registry).
    cases: dict[str, str] = {}


class ConfigInfo(_Model):
    id: str
    description: str
    provider: str
    routes: dict[str, str]  # Task -> "provider:model"; empty for the fake provider.
    prompts: dict[str, str]
    patient_engine: str
    feedback_language: str
    voice: dict[str, str] = Field(default_factory=dict)  # voice-sim: target, engines, pacing.
    sha256: str


class ReliabilityEntry(_Model):
    source: str
    producer: str
    precision: float | None
    observations: int


class Report(_Model):
    report_version: str
    suite: str
    split: str
    gate_mode: bool
    started_at: datetime
    duration_ms: int
    repetitions: int
    dataset: DatasetInfo
    config: ConfigInfo
    versions: Versions
    # Requested and returned models seen on the calls, per operation.
    models_recorded: dict[str, list[str]]
    models_returned: dict[str, list[str]]
    metrics: dict[str, float | None]
    segments: dict[str, dict[str, float | None]]  # Family -> metrics.
    reliability: list[ReliabilityEntry]
    critical_errors: list[str]
    cost: CostSummary
    latency: dict[str, LatencySummary]
    gate: GateResult | None
    items: list[ItemEntry]


def baseline_mismatch(
    baseline: Report,
    *,
    suite: str,
    split: str,
    manifest_sha256: str,
    item_ids: Sequence[str] | None = None,
    cases: Mapping[str, str] | None = None,
) -> str | None:
    """Why `baseline` did not run on the same inputs, or None.

    Its critical errors (and its metrics, in a comparison) only mean something for the same
    dataset version, split, items and played cases. `item_ids` and `cases` left out are not
    checked (before a run, when they are not known yet).
    """
    if (baseline.suite, baseline.split, baseline.dataset.manifest_sha256) != (
        suite,
        split,
        manifest_sha256,
    ):
        return "ne porte pas sur le même jeu, la même version et la même partition que la baseline"
    if item_ids is not None and [i.item_id for i in baseline.items] != list(item_ids):
        return "les items diffèrent de la baseline"
    if cases is not None and baseline.dataset.cases != dict(cases):
        return "les versions de cas diffèrent de la baseline"
    return None


def write_report(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


def read_report(path: Path) -> Report:
    return Report.model_validate_json(path.read_text(encoding="utf-8"))


def diagnosis_text(report: Report, *, limit: int = 60) -> str:
    """The items that failed or changed between repetitions, with what each repetition reported.

    For reading a dev-split run in a log without downloading the report. The holdout keeps its
    content out of reports (`detail` is None), so it has nothing to show and says so.
    """
    if report.gate_mode or report.split != "dev":
        return "Diagnostic : réservé au split dev ; le holdout garde son contenu hors des rapports."
    lines: list[str] = []
    shown = 0
    for item in report.items:
        unstable = len({run.signature for run in item.runs}) > 1
        failed = any(run.outcomes or not run.usable for run in item.runs)
        if not (unstable or failed):
            continue
        shown += 1
        if shown > limit:
            continue
        flags = ", ".join(([] if not unstable else ["instable"]) + (["en échec"] if failed else []))
        lines.append(f"- {item.item_id} ({item.family}, {item.case_id}) : {flags}")
        for run in item.runs:
            detail = run.detail or {}
            reported = [
                f"{r['category']}@{r['turns']} {r['verdict']} « {str(r['text'])[:90]} »"
                for r in detail.get("reported_errors", [])
            ]
            missed = [
                f"{m.get('category')}@{m.get('turn')}" for m in detail.get("missed_errors", [])
            ]
            switches = (
                f" ; alternances {detail.get('reported_code_switches', [])}"
                f" attendues {detail.get('expected_code_switches', [])}"
                if detail.get("reported_code_switches") or detail.get("expected_code_switches")
                else ""
            )
            sections = (
                f" ; sections oubliées {detail.get('forgotten_sections', [])}"
                f" attendues {detail.get('expected_missing_sections', [])}"
                if "section_mismatch" in run.outcomes
                else ""
            )
            lines.append(
                f"    passage {run.repetition} : "
                f"signalé [{'; '.join(reported) or '-'}] manqué [{', '.join(missed) or '-'}]"
                f"{switches}{sections}"
                f"{' ; ' + ', '.join(run.outcomes) if run.outcomes else ''}"
            )
    if shown > limit:
        lines.append(f"… {shown - limit} autre(s) item(s) non affichés")
    head = f"Diagnostic de {report.suite} ({report.split}) : {shown} item(s) à regarder"
    return "\n".join([head, *lines])


def summarize_cost(
    calls: Sequence[CallEntry], runs: int, pricing_version: str | None
) -> CostSummary:
    unknown = [c for c in calls if c.cost_micro_eur is None]
    known = sum(c.cost_micro_eur or 0 for c in calls)
    total = None if unknown else known
    per_operation: dict[str, dict[str, int | None]] = {}
    for operation in sorted({c.operation for c in calls}):
        selected = [c for c in calls if c.operation == operation]
        missing = sum(1 for c in selected if c.cost_micro_eur is None)
        per_operation[operation] = {
            "calls": len(selected),
            "unknown_calls": missing,
            "total_micro_eur": (None if missing else sum(c.cost_micro_eur or 0 for c in selected)),
        }
    return CostSummary(
        pricing_version=pricing_version,
        calls=len(calls),
        unknown_calls=len(unknown),
        unknown_components=sorted({u for c in unknown for u in c.unknown_components}),
        known_micro_eur=known,
        total_micro_eur=total,
        runs=runs,
        mean_micro_eur_per_case=None if total is None or not runs else round(total / runs),
        per_operation=per_operation,
    )


def summarize_latency(calls: Iterable[CallEntry]) -> dict[str, LatencySummary]:
    by_operation: dict[str, list[int]] = {}
    for call in calls:
        by_operation.setdefault(call.operation, []).append(call.latency_ms)
    return {
        operation: LatencySummary(
            calls=len(values),
            p50_ms=_quantile(values, 0.5),
            p95_ms=_quantile(values, 0.95),
            max_ms=max(values) if values else None,
        )
        for operation, values in sorted(by_operation.items())
    }


def _quantile(values: list[int], q: float) -> int | None:
    """Nearest-rank quantile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def format_micro_eur(value: int | None) -> str:
    return "inconnu" if value is None else f"{value / 1_000_000:.6f} €"
