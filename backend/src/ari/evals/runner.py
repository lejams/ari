"""Runs one suite on one configuration and builds its report.

Each item is run `repetitions` times through the real ARI service. A structured-output
failure (provider error or service guard) is retried once, as the gate measures "usable
structure after one retry"; every attempt is recorded and costed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.services.costing import PricingTable, cost_of
from ari.application.services.evaluation import EVALUATION_SCHEMA_VERSION
from ari.domain.errors import ProviderError
from ari.domain.models import MedicalCase
from ari.evals import gates as gate_rules
from ari.evals.configs import ConfigError, EvalConfig
from ari.evals.manifest import Dataset, Split
from ari.evals.metrics import COMMON_RATIOS, Ratio, compute, total
from ari.evals.report import (
    REPORT_VERSION,
    CallEntry,
    ConfigInfo,
    DatasetInfo,
    ItemEntry,
    PromptVersion,
    ReliabilityEntry,
    Report,
    RunEntry,
    Versions,
    baseline_mismatch,
    summarize_cost,
    summarize_latency,
)
from ari.evals.scorers import CallRecord, EvalItem, RecordingProvider, RunContext, Suite

# Operation that each prompt role's calls carry, to name the producer of observations.
ROLE_OPERATIONS = {"patient": "patient_simulation", "evaluation": "session_evaluation"}
ATTEMPTS = 2  # One retry.


@dataclass(frozen=True, slots=True)
class RunRequest:
    suite: Suite
    dataset: Dataset
    split: Split
    gate_mode: bool
    config: EvalConfig
    config_sha256: str
    provider: LLMProvider
    prompts: Mapping[str, VersionedPrompt]
    pricing: PricingTable | None
    repetitions: int
    gates: gate_rules.Gates | None = None
    baseline: Report | None = None
    concurrency: int = 4
    code_version: str | None = None


def ratios_for(suite: Suite, keys: Sequence[str]) -> list[Ratio]:
    return [*suite.ratios(keys), *COMMON_RATIOS]


async def run(request: RunRequest) -> Report:
    suite, dataset = request.suite, request.dataset
    if request.gate_mode and (request.gates is None or dataset.ref not in request.gates.suites):
        raise ConfigError(f"{dataset.ref} n'a pas de seuils dans gates.yaml : run gate refusé")
    if request.gate_mode and request.baseline is not None:
        mismatch = baseline_mismatch(
            request.baseline,
            suite=dataset.ref,
            split=request.split,
            manifest_sha256=dataset.manifest_hash,
        )
        if mismatch is not None:
            raise ConfigError(f"baseline {request.baseline.config.id} : {mismatch}")
    rows = dataset.read_split(request.split, gate=request.gate_mode)
    items = [suite.parse_item(row) for row in rows]
    cases: dict[str, MedicalCase] = {}
    for item in items:
        if item.case_id not in dataset.cases:
            raise ValueError(f"{item.id}: case {item.case_id} is not in cases.json")
        if item.case_id not in cases:
            cases[item.case_id] = dataset.cases[item.case_id].to_medical_case()

    started_at = datetime.now(UTC)
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(max(1, request.concurrency))

    async def guarded(item: EvalItem, repetition: int) -> RunEntry:
        async with semaphore:
            return await _run_once(request, item, cases[item.case_id], repetition)

    runs = await asyncio.gather(
        *(guarded(item, rep) for item in items for rep in range(1, request.repetitions + 1))
    )
    by_item: dict[str, list[RunEntry]] = {}
    for item, entry in zip(
        (item for item in items for _ in range(request.repetitions)), runs, strict=True
    ):
        by_item.setdefault(item.id, []).append(entry)
    entries = [item_entry(item, by_item[item.id], request.repetitions) for item in items]

    summed = total(entry.counts for entry in entries)
    ratios = ratios_for(suite, list(summed))
    metrics = compute(ratios, summed)
    segments = {
        family: compute(ratios, total(e.counts for e in entries if e.family == family))
        for family in sorted({e.family for e in entries})
    }
    calls = [call for entry in entries for run_entry in entry.runs for call in run_entry.calls]
    models_recorded: dict[str, list[str]] = {}
    models_returned: dict[str, list[str]] = {}
    for call in calls:
        for seen, model in ((models_recorded, call.model), (models_returned, call.model_returned)):
            models = seen.setdefault(call.operation, [])
            if model is not None and model not in models:
                models.append(model)
    producers = {
        role: f"{prompt.version}@{'+'.join(models_recorded.get(ROLE_OPERATIONS[role], ['?']))}"
        for role, prompt in request.prompts.items()
    }
    reliability = [
        ReliabilityEntry(
            source=r.source, producer=r.producer, precision=r.precision, observations=r.observations
        )
        for r in suite.reliability(summed, producers)
    ]
    suite_ref = dataset.ref
    critical: list[str] = []
    gate = None
    if request.gates is not None and suite_ref in request.gates.suites:
        critical = gate_rules.critical_errors(
            {e.item_id: e.outcomes for e in entries},
            request.gates.suites[suite_ref].critical_outcomes,
        )
        if request.gate_mode:
            if request.baseline is not None:
                mismatch = baseline_mismatch(
                    request.baseline,
                    suite=suite_ref,
                    split=request.split,
                    manifest_sha256=dataset.manifest_hash,
                    item_ids=[e.item_id for e in entries],
                    cases={},
                )
                if mismatch is not None:
                    raise ConfigError(f"baseline {request.baseline.config.id} : {mismatch}")
            gate = gate_rules.evaluate(
                request.gates,
                suite_ref,
                metrics,
                critical,
                baseline_critical=request.baseline.critical_errors if request.baseline else None,
                baseline_id=request.baseline.config.id if request.baseline else None,
            )
    return Report(
        report_version=REPORT_VERSION,
        suite=suite_ref,
        split=request.split,
        gate_mode=request.gate_mode,
        started_at=started_at,
        duration_ms=int((time.perf_counter() - started) * 1000),
        repetitions=request.repetitions,
        dataset=DatasetInfo(
            ref=suite_ref,
            manifest_sha256=dataset.manifest_hash,
            synthetic=dataset.manifest.synthetic,
            split=request.split,
            items=len(items),
            families=sorted({item.family for item in items}),
            files=dict(dataset.manifest.files),
        ),
        config=ConfigInfo(
            id=request.config.id,
            description=request.config.description,
            provider=request.config.provider,
            routes=request.config.route_labels(),
            prompts={str(k): v for k, v in request.config.prompts.items()},
            patient_engine=request.config.patient_engine,
            feedback_language=request.config.feedback_language,
            sha256=request.config_sha256,
        ),
        versions=Versions(
            prompts={
                role: PromptVersion(version=p.version, sha256=p.content_hash)
                for role, p in request.prompts.items()
            },
            output_schemas={
                schema.__name__: hashlib.sha256(
                    json.dumps(schema.model_json_schema(), sort_keys=True).encode()
                ).hexdigest()
                for schema in suite.output_schemas(request.config.patient_engine)
            },
            evaluation_schema_version=(
                EVALUATION_SCHEMA_VERSION if "evaluation" in suite.prompt_roles else None
            ),
            scorer=suite.scorer_version,
            gates=request.gates.version if request.gates else None,
            pricing=request.pricing.version if request.pricing else None,
            code=request.code_version,
        ),
        models_recorded=models_recorded,
        models_returned=models_returned,
        metrics=metrics,
        segments=segments,
        reliability=reliability,
        critical_errors=critical,
        cost=summarize_cost(calls, len(runs), request.pricing.version if request.pricing else None),
        latency=summarize_latency(calls),
        gate=gate,
        items=entries,
    )


async def _run_once(
    request: RunRequest, item: EvalItem, case: MedicalCase, repetition: int
) -> RunEntry:
    suite = request.suite
    recorder = RecordingProvider(request.provider)
    context = RunContext(
        prompts=request.prompts,
        feedback_language=request.config.feedback_language,
        run_key=f"evals:{suite.name}:{item.id}:r{repetition}",
        patient_engine=request.config.patient_engine,
    )
    value: object | None = None
    errors: list[str] = []
    attempts = 0
    for attempt in range(1, ATTEMPTS + 1):
        attempts = attempt
        try:
            value = await suite.execute(item, case, recorder, context)
            break
        except ProviderError as exc:
            errors.append(str(exc)[:300])
    score = suite.score(item, case, value, recorder.calls)
    counts = dict(score.counts)
    counts["runs.total"] = 1.0
    counts["runs.usable"] = 1.0 if value is not None else 0.0
    counts["runs.first_try"] = 1.0 if value is not None and attempts == 1 else 0.0
    return RunEntry(
        repetition=repetition,
        attempts=attempts,
        usable=value is not None,
        verdict=score.verdict and value is not None,
        outcomes=list(score.outcomes) or ([] if value is not None else ["unusable_output"]),
        counts=counts,
        signature=score.signature,
        errors=errors,
        calls=[call_entry(call, request.pricing) for call in recorder.calls],
        detail=None if request.split == "holdout" else score.detail,
    )


def call_entry(call: CallRecord, pricing: PricingTable | None) -> CallEntry:
    execution = call.execution
    if execution is None:
        return CallEntry(
            operation="unknown",
            provider="unknown",
            model="unknown",
            model_returned=None,
            status="failed",
            latency_ms=0,
            cost_micro_eur=None,
            unknown_components=["execution"],
            usage={},
        )
    cost = cost_of(execution, pricing)
    return CallEntry(
        operation=execution.operation,
        provider=execution.provider,
        model=execution.model,
        model_returned=execution.model_returned,
        status=execution.status.value,
        latency_ms=execution.latency_ms,
        cost_micro_eur=cost.cost_micro_eur,
        unknown_components=list(cost.unknown_components),
        usage=dict(execution.usage),
    )


def item_entry(item: EvalItem, runs: list[RunEntry], repetitions: int) -> ItemEntry:
    counts = total(run.counts for run in runs)
    if repetitions >= 2:
        counts["stability.items"] = 1.0
        counts["stability.changed"] = 1.0 if len({r.signature for r in runs}) > 1 else 0.0
    outcomes = sorted({o for run in runs for o in run.outcomes})
    return ItemEntry(
        item_id=item.id,
        family=item.family,
        case_id=item.case_id,
        verdict=sum(run.verdict for run in runs) * 2 > len(runs),
        outcomes=outcomes,
        counts=counts,
        runs=runs,
    )
