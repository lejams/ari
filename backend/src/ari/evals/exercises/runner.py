"""Runs the exercise checks on a dataset and builds the usual report.

What runs is the production chain, `ExerciseChecker`: the mechanical checks, the blind solver,
the judge, on the models of the configuration. An exercise with a known defect that comes out
accepted is a false acceptance (the critical outcome of the suite); a good exercise that comes
out refused is a false rejection (a cost, not a danger).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ari.application.exercise_schemas import RUBRIC_VERSION, BlindSolveSchema, JudgeSchema
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.services.costing import PricingTable
from ari.application.services.exercise_bank import OP_JUDGE, OP_SOLVE, CheckOutcome, ExerciseChecker
from ari.domain.errors import ProviderError
from ari.domain.language_exercises import MAX_LEVEL_RELABEL, MECHANICAL_CHECKS_VERSION, level_gap
from ari.evals import gates as gate_rules
from ari.evals.exercises.dataset import MECHANICAL_DEFECTS, ExerciseDataset, ExerciseItem
from ari.evals.manifest import Split
from ari.evals.metrics import COMMON_RATIOS, Ratio, compute, total
from ari.evals.report import (
    REPORT_VERSION,
    ConfigInfo,
    DatasetInfo,
    ItemEntry,
    PromptVersion,
    Report,
    RunEntry,
    Versions,
    baseline_mismatch,
    summarize_cost,
    summarize_latency,
)
from ari.evals.runner import call_entry, item_entry
from ari.evals.scorers import CallRecord, EvalItem, RecordingProvider
from ari.infrastructure.exercises.models import ExerciseConfigError, ExerciseModelsConfig

SCORER_PREFIX = "language-exercises-scorer-v3"
ATTEMPTS = 2


def scorer_version(
    rubric_version: str = RUBRIC_VERSION, mechanical: str = MECHANICAL_CHECKS_VERSION
) -> str:
    """The scorer and the checks it exercises, so a report says what it measured."""
    return f"{SCORER_PREFIX}+{rubric_version}+{mechanical}"


def ratios_for(keys: Sequence[str]) -> list[Ratio]:
    ratios = [
        Ratio("false_accept_rate", ("defective.accepted",), ("defective.total",)),
        Ratio("false_reject_rate", ("valid.rejected",), ("valid.total",)),
        Ratio("mechanical_catch_rate", ("mechanical.rejected",), ("mechanical.total",)),
        # Information only, no threshold: valid exercises accepted with a level moved one step.
        Ratio("relabel_rate", ("valid.relabeled",), ("valid.total",)),
        # Information only: swapped-level exercises accepted within one step of their true level.
        Ratio(
            "level_swap_kept_within_one_step_rate",
            ("level_swap.kept_within_one_step",),
            ("defect.level_mismatch.total",),
        ),
    ]
    kinds = sorted({key.split(".")[1] for key in keys if key.startswith("defect.")})
    ratios += [
        Ratio(f"false_accept_rate.{kind}", (f"defect.{kind}.accepted",), (f"defect.{kind}.total",))
        for kind in kinds
    ]
    return [*ratios, *COMMON_RATIOS]


def score(
    item: ExerciseItem, outcome: CheckOutcome | None, true_level: str | None = None
) -> tuple[dict[str, float], list[str], str]:
    """Counts, outcomes (failure kinds) and the signature of one run.

    `true_level` is the level of the exercise before its level was swapped (the level of the valid
    exercise of the same family). A swapped level that comes out accepted is a false acceptance
    only when the level it is stored under is more than one step from the true one: one step is
    the imprecision the bank tolerates for a valid exercise too (`MAX_LEVEL_RELABEL`). Without a
    true level the measure stays strict.
    """
    if outcome is None:  # No usable answer even after a retry: `runs.usable` counts it.
        return {"unusable": 1.0}, [], json.dumps({"usable": False})
    counts: dict[str, float] = {}
    truth = item.truth
    outcomes: list[str] = []
    if truth.valid:
        counts["valid.total"] = 1.0
    else:
        assert truth.defect is not None
        counts["defective.total"] = 1.0
        counts[f"defect.{truth.defect}.total"] = 1.0
        if truth.defect in MECHANICAL_DEFECTS:
            counts["mechanical.total"] = 1.0
    if outcome.stage == "mechanical" and truth.defect in MECHANICAL_DEFECTS:
        counts["mechanical.rejected"] = 1.0
    if truth.valid and not outcome.accepted:
        counts["valid.rejected"] = 1.0
        outcomes.append("false_reject")
    if truth.valid and outcome.accepted and outcome.level != item.candidate.level:
        counts["valid.relabeled"] = 1.0
    if (
        truth.defect == "level_mismatch"
        and outcome.accepted
        and true_level is not None
        and outcome.level is not None
        and level_gap(outcome.level, true_level) <= MAX_LEVEL_RELABEL
    ):
        counts["level_swap.kept_within_one_step"] = 1.0
    elif not truth.valid and outcome.accepted:
        counts["defective.accepted"] = 1.0
        counts[f"defect.{truth.defect}.accepted"] = 1.0
        outcomes.append("false_accept")
    signature = json.dumps({"accepted": outcome.accepted, "stage": outcome.stage})
    return counts, outcomes, signature


@dataclass(frozen=True, slots=True)
class ExerciseRunRequest:
    dataset: ExerciseDataset
    split: Split
    gate_mode: bool
    config: ExerciseModelsConfig
    config_sha256: str
    provider: LLMProvider
    prompts: dict[str, VersionedPrompt]
    pricing: PricingTable | None
    repetitions: int
    gates: gate_rules.Gates | None = None
    baseline: Report | None = None
    concurrency: int = 4
    code_version: str | None = None


async def run_exercises(request: ExerciseRunRequest) -> Report:
    dataset, config = request.dataset, request.config
    suite_ref = dataset.ref
    if request.gate_mode and (request.gates is None or suite_ref not in request.gates.suites):
        raise ExerciseConfigError(
            f"{suite_ref} n'a pas de seuils dans gates.yaml : run gate refusé"
        )
    if request.gate_mode and request.baseline is not None:
        mismatch = baseline_mismatch(
            request.baseline,
            suite=suite_ref,
            split=request.split,
            manifest_sha256=dataset.manifest_hash,
        )
        if mismatch is not None:
            raise ExerciseConfigError(f"baseline {request.baseline.config.id} : {mismatch}")
    items = dataset.read_split(request.split, gate=request.gate_mode)
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(max(1, request.concurrency))

    # The level an exercise had before the swap is that of the valid exercise of its family.
    true_levels = {i.family: i.candidate.level for i in items if i.truth.valid}

    async def guarded(item: ExerciseItem, repetition: int) -> RunEntry:
        async with semaphore:
            return await _run_once(request, item, repetition, true_levels.get(item.family))

    runs = await asyncio.gather(
        *(guarded(item, rep) for item in items for rep in range(1, request.repetitions + 1))
    )
    by_item: dict[str, list[RunEntry]] = {}
    for item, entry in zip(
        (item for item in items for _ in range(request.repetitions)), runs, strict=True
    ):
        by_item.setdefault(item.id, []).append(entry)
    entries = [item_entry_for(item, by_item[item.id], request.repetitions) for item in items]
    summed = total(entry.counts for entry in entries)
    ratios = ratios_for(list(summed))
    metrics = compute(ratios, summed)
    segments = {
        family: compute(ratios, total(e.counts for e in entries if e.family == family))
        for family in sorted({e.family for e in entries})
    }
    calls = [call for entry in entries for run in entry.runs for call in run.calls]
    models_recorded: dict[str, list[str]] = {}
    models_returned: dict[str, list[str]] = {}
    for call in calls:
        for seen, model in ((models_recorded, call.model), (models_returned, call.model_returned)):
            names = seen.setdefault(call.operation, [])
            if model is not None and model not in names:
                names.append(model)
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
                    raise ExerciseConfigError(f"baseline {request.baseline.config.id} : {mismatch}")
            gate = gate_rules.evaluate(
                request.gates,
                suite_ref,
                metrics,
                critical,
                baseline_critical=request.baseline.critical_errors if request.baseline else None,
                baseline_id=request.baseline.config.id if request.baseline else None,
                repetitions=request.repetitions,
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
            files={str(k): v for k, v in dataset.manifest.files.items()},
        ),
        config=ConfigInfo(
            id=config.id,
            description=config.description,
            provider=config.provider,
            routes=config.route_labels(),
            prompts=config.prompts.model_dump(),
            patient_engine="none",
            feedback_language="fr-FR",
            sha256=request.config_sha256,
        ),
        versions=Versions(
            prompts={
                role: PromptVersion(version=p.version, sha256=p.content_hash)
                for role, p in request.prompts.items()
                if role in ("solver", "judge")
            },
            output_schemas={
                schema.__name__: hashlib.sha256(
                    json.dumps(schema.model_json_schema(), sort_keys=True).encode()
                ).hexdigest()
                for schema in (BlindSolveSchema, JudgeSchema)
            },
            evaluation_schema_version=None,
            scorer=scorer_version(),
            gates=request.gates.version if request.gates else None,
            pricing=request.pricing.version if request.pricing else None,
            code=request.code_version,
        ),
        models_recorded=models_recorded,
        models_returned=models_returned,
        metrics=metrics,
        segments=segments,
        reliability=[],
        critical_errors=critical,
        cost=summarize_cost(calls, len(runs), request.pricing.version if request.pricing else None),
        latency=summarize_latency(calls),
        gate=gate,
        items=entries,
    )


def item_entry_for(item: ExerciseItem, runs: list[RunEntry], repetitions: int) -> ItemEntry:
    return item_entry(EvalItem(id=item.id, family=item.family, case_id="none"), runs, repetitions)


async def _run_once(
    request: ExerciseRunRequest, item: ExerciseItem, repetition: int, true_level: str | None = None
) -> RunEntry:
    recorder = RecordingProvider(request.provider)
    checker = ExerciseChecker(
        recorder,
        solver_prompt=request.prompts["solver"],
        judge_prompt=request.prompts["judge"],
        author_model=request.config.author_identity,
        passes=request.config.check_passes,
    )
    outcome: CheckOutcome | None = None
    errors: list[str] = []
    try:
        outcome = await checker.check(
            item.candidate, run_id=f"evals:{request.dataset.ref}:{item.id}:r{repetition}"
        )
    except ProviderError as exc:
        errors.append(str(exc)[:400])
    counts, outcomes, signature = score(item, outcome, true_level)
    counts["runs.total"] = 1.0
    counts["runs.usable"] = 1.0 if outcome is not None else 0.0
    counts["runs.first_try"] = 1.0 if outcome is not None and not _retried(recorder.calls) else 0.0
    return RunEntry(
        repetition=repetition,
        attempts=2 if _retried(recorder.calls) else 1,
        usable=outcome is not None,
        verdict=not outcomes and outcome is not None,
        outcomes=outcomes or ([] if outcome is not None else ["unusable_output"]),
        counts=counts,
        signature=signature,
        errors=errors,
        calls=[call_entry(call, request.pricing) for call in recorder.calls],
        detail=None
        if request.split == "holdout"
        else {
            "truth": item.truth.model_dump(mode="json"),
            "key_index": item.candidate.answer_index,
            "level": item.candidate.level,
            "true_level": true_level,
            "stem": item.candidate.stem,
            "accepted": outcome.accepted if outcome else None,
            "stage": outcome.stage if outcome else None,
            "issues": list(outcome.issues) if outcome else [],
            **(outcome.detail if outcome else {}),
        },
    )


def _retried(calls: Sequence[CallRecord]) -> bool:
    return any(call.value is None for call in calls)


__all__ = ["OP_JUDGE", "OP_SOLVE", "ExerciseRunRequest", "run_exercises", "scorer_version"]
