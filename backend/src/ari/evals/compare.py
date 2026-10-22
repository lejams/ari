"""Baseline against candidates, and the cost / quality frontier (plan, action 8, point 5bis).

For each candidate: metric deltas with paired bootstrap intervals, items whose verdict
changes, new critical errors. Then every configuration is gated again with the current
`gates.yaml`, and the ones that pass every gate, without a new critical error against the
baseline, are ranked by increasing mean cost per case: the first one is the recommendation.
A configuration whose cost is unknown is never recommended as the cheapest: it is listed
after the ranked ones with the reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ari.evals import gates as gate_rules
from ari.evals.metrics import COMMON_RATIOS, Ratio, bootstrap_interval, paired_difference_interval
from ari.evals.report import Report, baseline_mismatch, format_micro_eur
from ari.evals.runner import ratios_for
from ari.evals.suites import SUITES
from ari.evals.voice.scoring import RATIOS as VOICE_RATIOS

VOICE_SUITE = "voice-sim"

COMPARE_VERSION = "evals-compare-v1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricValue(_Model):
    value: float | None
    interval: tuple[float, float] | None


class MetricDelta(_Model):
    baseline: float | None
    candidate: float | None
    delta: float | None
    delta_interval: tuple[float, float] | None


class VerdictChange(_Model):
    item_id: str
    baseline: Literal["pass", "fail"]
    candidate: Literal["pass", "fail"]


class ConfigSummary(_Model):
    config_id: str
    routes: dict[str, str]
    prompts: dict[str, str]
    gate: gate_rules.GateResult
    mean_micro_eur_per_case: int | None
    unknown_cost_calls: int
    metrics: dict[str, MetricValue]


class CandidateComparison(_Model):
    config_id: str
    metric_deltas: dict[str, MetricDelta]
    verdict_changes: list[VerdictChange]
    new_critical_errors: list[str]


class RankedConfig(_Model):
    rank: int | None  # None: passes the gates but its cost is unknown, so it is not ranked.
    config_id: str
    mean_micro_eur_per_case: int | None
    note: str


class Comparison(_Model):
    compare_version: str
    suite: str
    split: str
    dataset_manifest_sha256: str
    gates_version: str
    baseline: str
    configs: list[ConfigSummary]
    candidates: list[CandidateComparison]
    ranking: list[RankedConfig]
    rejected: dict[str, list[str]]  # Config id -> why it cannot be adopted.
    recommendation: str | None
    recommendation_reason: str


class CompareError(Exception):
    pass


def compare(reports: Sequence[Report], gates: gate_rules.Gates) -> Comparison:
    if len(reports) < 2:
        raise CompareError("comparer exige une baseline et au moins un candidat")
    baseline, candidates = reports[0], list(reports[1:])
    for report in candidates:
        mismatch = baseline_mismatch(
            baseline,
            suite=report.suite,
            split=report.split,
            manifest_sha256=report.dataset.manifest_sha256,
            item_ids=[i.item_id for i in report.items],
            cases=report.dataset.cases,
        )
        if mismatch is not None:
            raise CompareError(f"{report.config.id} : {mismatch}")
    ids = [r.config.id for r in reports]
    if len(ids) != len(set(ids)):
        raise CompareError("deux rapports portent le même identifiant de configuration")
    suite_name = baseline.suite.partition("@")[0]
    if suite_name != VOICE_SUITE and suite_name not in SUITES:
        raise CompareError(f"{baseline.suite} : jeu inconnu")
    if baseline.suite not in gates.suites:
        raise CompareError(f"{baseline.suite} n'a pas de seuils dans gates.yaml")
    critical_kinds = gates.suites[baseline.suite].critical_outcomes

    def critical_of(report: Report) -> list[str]:
        return gate_rules.critical_errors(
            {e.item_id: e.outcomes for e in report.items}, critical_kinds
        )

    baseline_critical = critical_of(baseline)
    keys = sorted({k for r in reports for e in r.items for k in e.counts})
    # voice-sim reports come from their own runner, with their own ratios (latency stays in
    # the report's metrics and its gate thresholds).
    ratios = (
        [*VOICE_RATIOS, *COMMON_RATIOS]
        if suite_name == VOICE_SUITE
        else ratios_for(SUITES[suite_name], keys)
    )
    summaries: list[ConfigSummary] = []
    for report in reports:
        own_critical = critical_of(report)
        gate = gate_rules.evaluate(
            gates,
            baseline.suite,
            report.metrics,
            own_critical,
            baseline_critical=baseline_critical,
            baseline_id=baseline.config.id,
            # Re-gated here whatever mode produced the report: fewer repetitions than the
            # current gates.yaml requires are never promoted.
            repetitions=report.repetitions,
        )
        summaries.append(
            ConfigSummary(
                config_id=report.config.id,
                routes=report.config.routes,
                prompts=report.config.prompts,
                gate=gate,
                mean_micro_eur_per_case=report.cost.mean_micro_eur_per_case,
                unknown_cost_calls=report.cost.unknown_calls,
                metrics=_metric_values(report, ratios),
            )
        )
    comparisons = [
        _compare_pair(baseline, candidate, ratios, critical_of(candidate), baseline_critical)
        for candidate in candidates
    ]
    ranking, rejected, recommendation, reason = _rank(summaries)
    return Comparison(
        compare_version=COMPARE_VERSION,
        suite=baseline.suite,
        split=baseline.split,
        dataset_manifest_sha256=baseline.dataset.manifest_sha256,
        gates_version=gates.version,
        baseline=baseline.config.id,
        configs=summaries,
        candidates=comparisons,
        ranking=ranking,
        rejected=rejected,
        recommendation=recommendation,
        recommendation_reason=reason,
    )


def _metric_values(report: Report, ratios: Sequence[Ratio]) -> dict[str, MetricValue]:
    per_item = [item.counts for item in report.items]
    return {
        ratio.name: MetricValue(
            value=report.metrics.get(ratio.name), interval=bootstrap_interval(ratio, per_item)
        )
        for ratio in ratios
    }


def _compare_pair(
    baseline: Report,
    candidate: Report,
    ratios: Sequence[Ratio],
    candidate_critical: list[str],
    baseline_critical: list[str],
) -> CandidateComparison:
    before = [item.counts for item in baseline.items]
    after = [item.counts for item in candidate.items]
    deltas: dict[str, MetricDelta] = {}
    for ratio in ratios:
        b, c = baseline.metrics.get(ratio.name), candidate.metrics.get(ratio.name)
        deltas[ratio.name] = MetricDelta(
            baseline=b,
            candidate=c,
            delta=None if b is None or c is None else round(c - b, 6),
            delta_interval=paired_difference_interval(ratio, before, after),
        )
    changes = [
        VerdictChange(
            item_id=b.item_id,
            baseline="pass" if b.verdict else "fail",
            candidate="pass" if c.verdict else "fail",
        )
        for b, c in zip(baseline.items, candidate.items, strict=True)
        if b.verdict != c.verdict
    ]
    return CandidateComparison(
        config_id=candidate.config.id,
        metric_deltas=deltas,
        verdict_changes=changes,
        new_critical_errors=sorted(set(candidate_critical) - set(baseline_critical)),
    )


def _rank(
    summaries: Sequence[ConfigSummary],
) -> tuple[list[RankedConfig], dict[str, list[str]], str | None, str]:
    rejected: dict[str, list[str]] = {}
    passing: list[ConfigSummary] = []
    for summary in summaries:
        reasons = [
            f"{check.metric} {check.bound} : "
            + ("non mesuré" if check.value is None else f"{check.value}")
            for check in summary.gate.checks
            if check.status != "passed"
        ]
        reasons += [f"nouvelle erreur critique {e}" for e in summary.gate.new_critical_errors]
        if reasons:
            rejected[summary.config_id] = reasons
        else:
            passing.append(summary)
    known = sorted(
        (s for s in passing if s.mean_micro_eur_per_case is not None),
        key=lambda s: (s.mean_micro_eur_per_case, s.config_id),
    )
    unknown = [s for s in passing if s.mean_micro_eur_per_case is None]
    ranking = [
        RankedConfig(
            rank=index,
            config_id=s.config_id,
            mean_micro_eur_per_case=s.mean_micro_eur_per_case,
            note="passe tous les seuils",
        )
        for index, s in enumerate(known, start=1)
    ] + [
        RankedConfig(
            rank=None,
            config_id=s.config_id,
            mean_micro_eur_per_case=None,
            note=f"passe les seuils, coût inconnu ({s.unknown_cost_calls} appel(s) sans tarif)",
        )
        for s in unknown
    ]
    if known:
        best = known[0]
        reason = (
            f"{best.config_id} est la configuration la moins chère parmi celles qui passent "
            f"tous les seuils ({format_micro_eur(best.mean_micro_eur_per_case)} par cas)."
        )
        return ranking, rejected, best.config_id, reason
    if unknown:
        return (
            ranking,
            rejected,
            None,
            "Des configurations passent les seuils mais leur coût est inconnu : compléter la "
            "grille tarifaire avant de choisir.",
        )
    return ranking, rejected, None, "Aucune configuration ne passe tous les seuils."


def render_markdown(comparison: Comparison) -> str:
    """Short French summary for the terminal and the pull request."""
    lines = [
        f"# Comparaison {comparison.suite} ({comparison.split})",
        "",
        f"Baseline : `{comparison.baseline}`. Seuils : `{comparison.gates_version}`.",
        "",
        "| Configuration | Seuils | Coût moyen par cas | Nouvelles erreurs critiques |",
        "|---|---|---|---|",
    ]
    for summary in comparison.configs:
        lines.append(
            f"| `{summary.config_id}` | "
            f"{'passés' if summary.gate.status == 'passed' else 'échoués'} | "
            f"{format_micro_eur(summary.mean_micro_eur_per_case)} | "
            f"{len(summary.gate.new_critical_errors)} |"
        )
    lines += ["", f"**Recommandation** : {comparison.recommendation_reason}", ""]
    for config_id, reasons in comparison.rejected.items():
        lines.append(f"- `{config_id}` écartée : " + " ; ".join(reasons[:6]))
        if len(reasons) > 6:
            lines[-1] += f" (et {len(reasons) - 6} autre(s))"
    for candidate in comparison.candidates:
        lines.append(
            f"- `{candidate.config_id}` : {len(candidate.verdict_changes)} item(s) changent de "
            "verdict par rapport à la baseline"
        )
    return "\n".join(lines) + "\n"
