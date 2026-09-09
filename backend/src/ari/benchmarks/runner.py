from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any, Literal

from ari.application.services.telemetry import nearest_rank
from ari.benchmarks.quality import evaluate_patient_invariants, evaluate_stt
from ari.benchmarks.schemas import (
    ChampionChallengerRulesSchema,
    VoiceBenchmarkSuiteSchema,
)
from ari.infrastructure.providers.openai.pricing import PRICING_VERSION

BenchmarkMode = Literal["offline", "live"]
BenchmarkConclusion = Literal["eligible", "not_eligible", "insufficient_data"]


@dataclass(frozen=True, slots=True)
class LiveBudget:
    maximum_usd: float
    reserved_usd: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum_usd) or self.maximum_usd <= 0:
            raise ValueError("Live budget maximum must be finite and strictly positive")
        if (
            not math.isfinite(self.reserved_usd)
            or self.reserved_usd < 0
            or self.reserved_usd > self.maximum_usd
        ):
            raise ValueError("Live budget reservation must be finite and within the ceiling")

    def reserve(
        self,
        predicted_cost_usd: float | None,
        *,
        conservative_reservation_usd: float | None = None,
    ) -> LiveBudget:
        reservation = predicted_cost_usd
        if reservation is None:
            reservation = conservative_reservation_usd
        if reservation is None or not math.isfinite(reservation) or reservation <= 0:
            raise RuntimeError("A live call with unknown cost requires a conservative reservation")
        if self.reserved_usd + reservation > self.maximum_usd:
            raise RuntimeError("Live benchmark cost ceiling would be exceeded")
        return LiveBudget(self.maximum_usd, self.reserved_usd + reservation)


def validate_live_guard(
    *,
    mode: BenchmarkMode,
    maximum_cost_usd: float | None,
    confirmed: bool,
    environment: dict[str, str] | None = None,
) -> None:
    if mode == "offline":
        return
    values = environment if environment is not None else os.environ
    if values.get("ARI_BENCHMARK_LIVE_ENABLED") != "true":
        raise RuntimeError("Live benchmark requires ARI_BENCHMARK_LIVE_ENABLED=true")
    if not values.get("OPENAI_API_KEY"):
        raise RuntimeError("Live benchmark requires an available provider key")
    if (
        maximum_cost_usd is None
        or not math.isfinite(maximum_cost_usd)
        or maximum_cost_usd <= 0
    ):
        raise RuntimeError("Live benchmark requires a strictly positive --max-cost-usd")
    if not confirmed:
        raise RuntimeError("Live benchmark requires explicit --confirm-live authorization")


def _stable_jitter(seed: int, stack_id: str, scenario_id: str, repetition: int) -> int:
    digest = hashlib.sha256(
        f"{seed}:{stack_id}:{scenario_id}:{repetition}".encode()
    ).digest()
    return int.from_bytes(digest[:2], "big") % 41 - 20


def _average(values: list[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return fmean(available) if available else None


def _stack_conclusion(
    summary: dict[str, Any], rules: ChampionChallengerRulesSchema
) -> tuple[BenchmarkConclusion, list[str]]:
    reasons: list[str] = []
    critical_hallucinations = int(summary["critical_hallucinations"])
    critical_alterations = int(summary["critical_alterations"])
    medical_recall = summary["medical_term_recall"]
    first_audio_p50 = summary["first_audio_p50_ms"]
    first_audio_p95 = summary["first_audio_p95_ms"]
    transport = str(summary["transport"])
    if critical_hallucinations > rules.critical_clinical_hallucinations_max:
        reasons.append("hallucination clinique critique observée")
    if critical_alterations > rules.critical_number_dose_negation_alterations_max:
        reasons.append("altération critique de dose, nombre ou négation")
    if isinstance(medical_recall, int | float) and medical_recall < rules.medical_term_recall_min:
        reasons.append("rappel des termes médicaux sous le seuil")
    p50_limit = (
        rules.realtime_first_audio_p50_max_ms
        if transport == "realtime"
        else rules.pipeline_first_audio_p50_max_ms
    )
    p95_limit = (
        rules.realtime_first_audio_p95_max_ms
        if transport == "realtime"
        else rules.pipeline_first_audio_p95_max_ms
    )
    if isinstance(first_audio_p50, int | float) and first_audio_p50 > p50_limit:
        reasons.append("latence p50 au-dessus du seuil")
    if isinstance(first_audio_p95, int | float) and first_audio_p95 > p95_limit:
        reasons.append("latence p95 au-dessus du seuil")
    if reasons:
        return "not_eligible", reasons

    missing: list[str] = []
    if int(summary["usable_turns"]) < rules.minimum_usable_turns:
        missing.append(f"moins de {rules.minimum_usable_turns} tours exploitables")
    if summary["cost_status"] not in {"exact", "estimated"}:
        missing.append("coût critique inconnu ou partiel")
    if summary["human_quality_score"] is None:
        missing.append("qualité humaine germanophone indisponible")
    if medical_recall is None or first_audio_p50 is None or first_audio_p95 is None:
        missing.append("métrique critique manquante")
    if missing:
        return "insufficient_data", missing
    return "eligible", []


def compare_champion_challenger(
    champion: dict[str, Any],
    challenger: dict[str, Any],
    rules: ChampionChallengerRulesSchema,
) -> dict[str, Any]:
    if champion["conclusion"] != "eligible" or challenger["conclusion"] != "eligible":
        return {
            "champion": champion["stack_id"],
            "challenger": challenger["stack_id"],
            "conclusion": "insufficient_data",
            "recommended_replacement": False,
            "reasons_fr": ["les deux stacks ne disposent pas de preuves éligibles"],
        }
    champion_cost = champion.get("total_cost_usd")
    challenger_cost = challenger.get("total_cost_usd")
    if not isinstance(champion_cost, int | float) or not isinstance(
        challenger_cost, int | float
    ):
        return {
            "champion": champion["stack_id"],
            "challenger": challenger["stack_id"],
            "conclusion": "insufficient_data",
            "recommended_replacement": False,
            "reasons_fr": ["coût total exact indisponible"],
        }
    reduction = 1.0 - challenger_cost / champion_cost if champion_cost > 0 else 0.0
    champion_human = champion.get("human_quality_score")
    challenger_human = challenger.get("human_quality_score")
    if not isinstance(champion_human, int | float) or not isinstance(
        challenger_human, int | float
    ):
        return {
            "champion": champion["stack_id"],
            "challenger": challenger["stack_id"],
            "conclusion": "insufficient_data",
            "recommended_replacement": False,
            "reasons_fr": ["revue humaine comparable indisponible"],
        }
    degradation = champion_human - challenger_human
    eligible = (
        reduction >= rules.minimum_cost_reduction
        and degradation <= rules.maximum_human_quality_degradation
    )
    return {
        "champion": champion["stack_id"],
        "challenger": challenger["stack_id"],
        "conclusion": "eligible" if eligible else "not_eligible",
        "recommended_replacement": eligible,
        "cost_reduction": reduction,
        "human_quality_degradation": degradation,
        "reasons_fr": [] if eligible else ["gain coût/qualité insuffisant"],
    }


def run_offline_benchmark(
    *,
    suite: VoiceBenchmarkSuiteSchema,
    rules: ChampionChallengerRulesSchema,
    stack_ids: tuple[str, ...],
    repetitions: int,
    seed: int,
    scenario_filters: tuple[str, ...] = (),
) -> dict[str, Any]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    selected = tuple(
        scenario
        for scenario in suite.scenarios
        if not scenario_filters
        or scenario.id in scenario_filters
        or any(tag in scenario.difficulty_tags for tag in scenario_filters)
    )
    if not selected:
        raise ValueError("No benchmark scenario matches the requested filter")
    summaries: list[dict[str, Any]] = []
    raw_results: list[dict[str, Any]] = []
    for stack_id in stack_ids:
        fixture = suite.offline_stack_fixtures.get(stack_id)
        if fixture is None:
            raise ValueError(f"Suite has no offline fixture for stack {stack_id}")
        stack_results: list[dict[str, Any]] = []
        for scenario in selected:
            observation = scenario.offline_observation
            stt = evaluate_stt(
                reference=scenario.transcript_gold_de,
                hypothesis=observation.transcript_de,
                medical_terms=tuple(scenario.medical_terms),
                critical_entities=tuple(scenario.critical_entities),
                numbers=tuple(scenario.numbers),
                doses=tuple(scenario.doses),
                negations=tuple(scenario.negations),
            )
            patient = evaluate_patient_invariants(
                observed_fact_ids=tuple(observation.patient_fact_ids),
                allowed_fact_ids=tuple(observation.allowed_fact_ids),
                required_fact_ids=tuple(scenario.required_fact_ids),
                forbidden_fact_ids=tuple(scenario.forbidden_fact_ids),
                out_of_scope_resisted=observation.out_of_scope_resisted,
                injection_resisted=observation.injection_resisted,
            )
            for repetition in range(repetitions):
                jitter = _stable_jitter(seed, stack_id, scenario.id, repetition)
                result = {
                    "stack_id": stack_id,
                    "scenario_id": scenario.id,
                    "scenario_version": scenario.version,
                    "repetition": repetition + 1,
                    "origin_status": scenario.origin_status,
                    "wer": stt.wer,
                    "medical_term_recall": stt.medical_term_recall,
                    "critical_entity_error_rate": stt.critical_entity_error_rate,
                    "number_alterations": list(stt.number_alterations),
                    "dose_alterations": list(stt.dose_alterations),
                    "negation_alterations": list(stt.negation_alterations),
                    "patient_invariants": asdict(patient),
                    "first_audio_ms": max(0, fixture.synthetic_first_audio_ms + jitter),
                    "turn_total_ms": max(0, fixture.synthetic_turn_total_ms + jitter),
                    "cost_status": "unknown",
                    "cost_usd": None,
                    "human_quality_score": None,
                }
                stack_results.append(result)
                raw_results.append(result)
        first_audio_values = [int(item["first_audio_ms"]) for item in stack_results]
        turn_total_values = [int(item["turn_total_ms"]) for item in stack_results]
        summary: dict[str, Any] = {
            "stack_id": stack_id,
            "stack_version": fixture.version,
            "fixture_configuration": fixture.model_dump(),
            "models": fixture.models,
            "transport": fixture.transport,
            "usable_turns": len(stack_results),
            "first_audio_p50_ms": nearest_rank(first_audio_values, 0.50),
            "first_audio_p95_ms": nearest_rank(first_audio_values, 0.95),
            "turn_total_p50_ms": nearest_rank(turn_total_values, 0.50),
            "turn_total_p95_ms": nearest_rank(turn_total_values, 0.95),
            "wer": _average([float(item["wer"]) for item in stack_results]),
            "medical_term_recall": _average(
                [
                    float(value) if isinstance(value, int | float) else None
                    for item in stack_results
                    for value in (item["medical_term_recall"],)
                ]
            ),
            "critical_entity_error_rate": _average(
                [
                    float(value) if isinstance(value, int | float) else None
                    for item in stack_results
                    for value in (item["critical_entity_error_rate"],)
                ]
            ),
            "number_alterations": sum(
                len(item["number_alterations"]) for item in stack_results
            ),
            "dose_alterations": sum(
                len(item["dose_alterations"]) for item in stack_results
            ),
            "negation_alterations": sum(
                len(item["negation_alterations"]) for item in stack_results
            ),
            "critical_alterations": sum(
                bool(item["number_alterations"])
                or bool(item["dose_alterations"])
                or bool(item["negation_alterations"])
                for item in stack_results
            ),
            "critical_hallucinations": sum(
                int(item["patient_invariants"]["critical_hallucination_count"])
                for item in stack_results
            ),
            "patient_invariant_failures": sum(
                not item["patient_invariants"]["disclosure_respected"]
                or not item["patient_invariants"]["out_of_scope_resisted"]
                or not item["patient_invariants"]["injection_resisted"]
                or bool(item["patient_invariants"]["missing_required_fact_ids"])
                for item in stack_results
            ),
            "errors": 0,
            "cost_status": "unknown",
            "total_cost_usd": None,
            "human_quality_score": None,
        }
        conclusion, reasons = _stack_conclusion(summary, rules)
        summary["conclusion"] = conclusion
        summary["reasons_fr"] = reasons
        summaries.append(summary)

    comparisons = [
        compare_champion_challenger(summaries[0], challenger, rules)
        for challenger in summaries[1:]
    ] if len(summaries) > 1 else []
    overall: BenchmarkConclusion
    if summaries and all(item["conclusion"] == "eligible" for item in summaries):
        overall = "eligible"
    elif any(item["conclusion"] == "not_eligible" for item in summaries):
        overall = "not_eligible"
    else:
        overall = "insufficient_data"
    return {
        "schema_version": "voice-benchmark-report-v1",
        "mode": "offline",
        "suite": {"id": suite.id, "version": suite.version},
        "rules": {"id": rules.id, "version": rules.version},
        "seed": seed,
        "repetitions": repetitions,
        "scenario_count": len(selected),
        "stacks": summaries,
        "turns": raw_results,
        "comparisons": comparisons,
        "conclusion": overall,
        "missing_data_fr": [
            "coûts fournisseurs réels",
            "latences réelles",
            "évaluation humaine germanophone",
            "enregistrements d'accents humains consentis",
        ],
        "summary_fr": (
            "Smoke benchmark hors ligne reproductible sur fixtures synthétiques. "
            "Aucune promotion n'est autorisée sans données réelles et revue humaine."
        ),
    }


def current_commit(project_root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def worktree_dirty(project_root: Path) -> bool | None:
    try:
        return bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=project_root,
            check=True, capture_output=True, text=True,
        ).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def write_reports(
    report: dict[str, Any],
    *,
    output_directory: Path,
    suite_path: Path,
    rules_path: Path,
    project_root: Path,
) -> dict[str, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    commit = current_commit(project_root)
    dirty = worktree_dirty(project_root)
    report = {**report, "generated_at": generated_at, "commit": commit, "worktree_dirty": dirty}
    run_name = f"{report['suite']['id']}-{report['mode']}"
    json_path = output_directory / f"{run_name}.json"
    markdown_path = output_directory / f"{run_name}.fr.md"
    manifest_path = output_directory / f"{run_name}.manifest.json"
    manifest = {
        "schema_version": "voice-benchmark-manifest-v1",
        "generated_at": generated_at,
        "commit": commit,
        "worktree_dirty": dirty,
        "suite_path": str(suite_path),
        "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        "suite": report["suite"],
        "rules_path": str(rules_path),
        "rules_sha256": hashlib.sha256(rules_path.read_bytes()).hexdigest(),
        "rules": report["rules"],
        "mode": report["mode"],
        "pricing_version": PRICING_VERSION,
        "seed": report["seed"],
        "repetitions": report["repetitions"],
        "scenario_count": report["scenario_count"],
        "stacks": [
            {
                "id": item["stack_id"],
                "version": item["stack_version"],
                "models": item["models"],
                "fixture_configuration": item["fixture_configuration"],
            }
            for item in report["stacks"]
        ],
    }
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Rapport benchmark vocal ARI",
        "",
        str(report["summary_fr"]),
        "",
        f"- Date : {generated_at}",
        f"- Commit : {commit or 'indisponible'}",
        f"- Modifications locales non committées : {dirty}",
        f"- Suite : {report['suite']['id']}@{report['suite']['version']}",
        f"- Mode : {report['mode']}",
        f"- Répétitions : {report['repetitions']}",
        f"- Conclusion : `{report['conclusion']}`",
        "",
        "## Résultats par stack",
        "",
    ]
    for item in report["stacks"]:
        lines.extend(
            [
                f"### {item['stack_id']}@{item['stack_version']}",
                "",
                f"- Modèles : {json.dumps(item['models'], ensure_ascii=False, sort_keys=True)}",
                f"- Tours exploitables : {item['usable_turns']}",
                "- Premier audio p50/p95 : "
                f"{item['first_audio_p50_ms']} / {item['first_audio_p95_ms']} ms",
                f"- WER : {item['wer']}",
                f"- Rappel terminologique : {item['medical_term_recall']}",
                "- Taux d'erreur des entités critiques : "
                f"{item['critical_entity_error_rate']}",
                "- Altérations nombres/doses/négations : "
                f"{item['number_alterations']} / {item['dose_alterations']} / "
                f"{item['negation_alterations']}",
                f"- Hallucinations cliniques critiques : {item['critical_hallucinations']}",
                f"- Échecs d'invariants patient : {item['patient_invariant_failures']}",
                f"- Erreurs d'exécution : {item['errors']}",
                f"- Coût : {item['cost_status']} ({item['total_cost_usd']})",
                f"- Conclusion : `{item['conclusion']}`",
                f"- Raisons : {', '.join(item['reasons_fr']) or 'aucune'}",
                "",
            ]
        )
    lines.extend(["## Comparaisons champion/challenger", ""])
    if report["comparisons"]:
        for comparison in report["comparisons"]:
            lines.extend(
                [
                    f"- {comparison['champion']} → {comparison['challenger']} : "
                    f"`{comparison['conclusion']}` ; remplacement recommandé : "
                    f"{comparison['recommended_replacement']} ; raisons : "
                    f"{', '.join(comparison['reasons_fr']) or 'aucune'}",
                ]
            )
    else:
        lines.append("- Aucune comparaison : une seule stack a été exécutée.")
    lines.append("")
    lines.extend(
        [
            "## Données manquantes",
            "",
            *[f"- {value}" for value in report["missing_data_fr"]],
            "",
            "Ces fixtures sont synthétiques : elles ne prouvent ni la qualité des accents "
            "humains, ni la naturalité ou l'empathie de la voix.",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path, "manifest": manifest_path}
