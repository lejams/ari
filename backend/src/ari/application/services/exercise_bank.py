"""Making exercises: the author proposes, the code checks, a second model solves, a judge reads.

The order is the order of cost, and every stage can only remove an exercise, never repair one:

1. mechanical checks (`domain.language_exercises.mechanical_issues`), no model;
2. the blind solver (the checker model, without the key): the exercise stays only if it finds
   the key and says that no other option is acceptable, and does not call a sentence absurd;
3. the judge (the same checker model, other prompt, with the key and a written rubric): the
   exercise stays only if every criterion passes. The judge supplies verdicts, the code
   computes the decision.

The checker is never the author's model: the guard compares the model recorded on the checker's
own calls with the author's, and refuses to go on when they are the same.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.exercise_batch import (
    AcceptedEntry,
    CallSummary,
    ExerciseBatch,
    RejectedEntry,
    SpecInfo,
    StageInfo,
)
from ari.application.exercise_schemas import (
    RUBRIC_VERSION,
    BlindSolveSchema,
    ExerciseBatchSchema,
    JudgeSchema,
)
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.domain.errors import InvalidStateError, ProviderError
from ari.domain.language_exercises import (
    EXERCISE_SCHEMA,
    MECHANICAL_CHECKS_VERSION,
    ExerciseCandidate,
    ExerciseReview,
    LanguageExercise,
    assemble,
    mechanical_issues,
)
from ari.domain.models import ExecutionRecord

OP_GENERATE = "exercise_generation"
OP_SOLVE = "exercise_blind_solve"
OP_JUDGE = "exercise_judge"
ATTEMPTS = 2  # One retry on a provider failure, as in the evaluation runner.


@dataclass(frozen=True, slots=True)
class ExerciseSpec:
    level: str
    category: str
    format: str
    setting_hint: str | None = None


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """What the checks decided about one candidate."""

    accepted: bool
    stage: str  # "accepted", or the stage that removed it.
    issues: tuple[str, ...] = ()
    solver: BlindSolveSchema | None = None
    judge: JudgeSchema | None = None
    executions: tuple[ExecutionRecord, ...] = ()

    @property
    def detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        if self.solver is not None:
            detail["solver"] = self.solver.model_dump(mode="json")
        if self.judge is not None:
            detail["judge"] = self.judge.model_dump(mode="json")
        return detail


def solver_accepts(candidate: ExerciseCandidate, verdict: BlindSolveSchema) -> bool:
    """The blind solver found the key, sees no other acceptable option, and no absurdity."""
    return (
        verdict.chosen_index == candidate.answer_index
        and verdict.acceptable_indices == [candidate.answer_index]
        and verdict.medically_plausible
    )


def _context(operation: str, run_id: str, content_hash: str, prompt: VersionedPrompt) -> Any:
    return ExecutionContext(
        session_id=f"exercises:{run_id}",
        operation=operation,
        case_version=EXERCISE_SCHEMA,
        case_hash=content_hash,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
    )


async def _generate[T: BaseModel](
    provider: LLMProvider,
    prompt: VersionedPrompt,
    operation: str,
    run_id: str,
    content_hash: str,
    payload: dict[str, Any],
    schema: type[T],
    *,
    sample: int = 0,
) -> tuple[T, ExecutionRecord, list[ExecutionRecord]]:
    """One structured call with one retry; returns the value, its execution and failed ones."""
    failed: list[ExecutionRecord] = []
    last: ProviderError | None = None
    for _ in range(ATTEMPTS):
        request = LLMRequest(
            messages=(
                {"role": "system", "content": prompt.content},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            context=_context(operation, run_id, content_hash, prompt),
            sample=sample,
        )
        try:
            result = await provider.generate_structured(request, schema)
        except ProviderError as exc:
            last = exc
            if isinstance(exc.execution, ExecutionRecord):
                failed.append(exc.execution)
            continue
        return result.value, result.execution, failed
    assert last is not None
    last.related_executions = tuple(failed)
    raise last


class ExerciseAuthor:
    def __init__(self, provider: LLMProvider, prompt: VersionedPrompt) -> None:
        self._provider = provider
        self._prompt = prompt

    @property
    def prompt(self) -> VersionedPrompt:
        return self._prompt

    async def draft(
        self, spec: ExerciseSpec, count: int, *, avoid: Sequence[str], run_id: str
    ) -> tuple[
        list[ExerciseCandidate | tuple[str, dict[str, Any]]], ExecutionRecord, list[ExecutionRecord]
    ]:
        """Candidates assembled by the code, the successful call and the failed ones before it.

        A draft that cannot be assembled comes back as ("draft", what it said) so the run can
        report it."""
        value, execution, failed = await _generate(
            self._provider,
            self._prompt,
            OP_GENERATE,
            run_id,
            f"{spec.level}:{spec.category}:{spec.format}",
            {
                "level": spec.level,
                "category": spec.category,
                "format": spec.format,
                "setting_hint": spec.setting_hint,
                "count": count,
                "avoid": list(avoid),
            },
            ExerciseBatchSchema,
        )
        candidates: list[ExerciseCandidate | tuple[str, dict[str, Any]]] = []
        for draft in value.exercises[:count]:
            try:
                candidates.append(
                    assemble(
                        level=spec.level,
                        category=spec.category,
                        format=spec.format,
                        stem=draft.stem,
                        key=draft.key,
                        distractors=draft.distractors,
                        explanation_fr=draft.explanation_fr,
                        setting=draft.setting,
                        context_fr=draft.context_fr,
                    )
                )
            except (ValidationError, ValueError):
                candidates.append(("draft", draft.model_dump(mode="json")))
        return candidates, execution, failed


class ExerciseChecker:
    """Blind solver then judge, on a model that is not the author's."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        solver_prompt: VersionedPrompt,
        judge_prompt: VersionedPrompt,
        author_model: tuple[str, str],  # (provider, model) of the author route.
    ) -> None:
        self._provider = provider
        self._solver_prompt = solver_prompt
        self._judge_prompt = judge_prompt
        self._author_model = author_model

    @property
    def solver_prompt(self) -> VersionedPrompt:
        return self._solver_prompt

    @property
    def judge_prompt(self) -> VersionedPrompt:
        return self._judge_prompt

    def _guard(self, execution: ExecutionRecord) -> None:
        if (execution.provider, execution.model) == self._author_model:
            raise InvalidStateError(
                "Le contrôle doit venir d'un autre modèle que l'auteur : "
                f"{execution.provider}:{execution.model} a répondu aux deux"
            )

    async def check(self, candidate: ExerciseCandidate, *, run_id: str) -> CheckOutcome:
        issues = mechanical_issues(candidate)
        if issues:
            return CheckOutcome(False, "mechanical", tuple(issues))
        executions: list[ExecutionRecord] = []
        content_hash = candidate.content_hash
        solved, execution, failed = await _generate(
            self._provider,
            self._solver_prompt,
            OP_SOLVE,
            run_id,
            content_hash,
            {
                "instruction_fr": candidate.instruction_fr,
                "situation_fr": candidate.context_fr,
                "stem": candidate.stem,
                "options": [{"index": i, "text": o} for i, o in enumerate(candidate.options)],
            },
            BlindSolveSchema,
        )
        executions.extend([*failed, execution])
        self._guard(execution)
        if len(solved.acceptable_indices) and max(solved.acceptable_indices) >= len(
            candidate.options
        ):
            return CheckOutcome(
                False,
                "blind_solver",
                ("solver_index_out_of_range",),
                solved,
                None,
                tuple(executions),
            )
        if not solver_accepts(candidate, solved):
            return CheckOutcome(False, "blind_solver", (), solved, None, tuple(executions))
        try:
            judged, execution, failed = await _generate(
                self._provider,
                self._judge_prompt,
                OP_JUDGE,
                run_id,
                content_hash,
                {
                    "rubric_version": RUBRIC_VERSION,
                    "level": candidate.level,
                    "category": candidate.category,
                    "format": candidate.format,
                    "instruction_fr": candidate.instruction_fr,
                    "context_fr": candidate.context_fr,
                    "stem": candidate.stem,
                    "options": [{"index": i, "text": o} for i, o in enumerate(candidate.options)],
                    "key_index": candidate.answer_index,
                    "explanation_fr": candidate.explanation_fr,
                    "setting": candidate.setting,
                },
                JudgeSchema,
            )
        except ProviderError as exc:
            # The solver call was made and billed: it stays in the batch's history.
            exc.related_executions = (*executions, *exc.related_executions)
            raise
        executions.extend([*failed, execution])
        self._guard(execution)
        if not judged.approved:
            failing = tuple(v.criterion for v in judged.verdicts if not v.passed)
            return CheckOutcome(False, "rubric_judge", failing, solved, judged, tuple(executions))
        return CheckOutcome(True, "accepted", (), solved, judged, tuple(executions))


def stage_info(stage: str, execution: ExecutionRecord, prompt: VersionedPrompt) -> StageInfo:
    return StageInfo.model_validate(
        {
            "stage": stage,
            "provider": execution.provider,
            "model": execution.model,
            "model_returned": execution.model_returned,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.content_hash,
        }
    )


def call_summary(execution: ExecutionRecord) -> CallSummary:
    return CallSummary(
        operation=execution.operation,
        provider=execution.provider,
        model=execution.model,
        status=execution.status.value,
        latency_ms=execution.latency_ms,
        usage=execution.usage,
    )


def reviews_of(outcome: CheckOutcome, solver: StageInfo, judge: StageInfo) -> list[ExerciseReview]:
    """The checks an accepted exercise went through, as rows of its review history."""
    assert outcome.accepted and outcome.solver is not None and outcome.judge is not None
    return [
        ExerciseReview(
            "blind_solver",
            f"{solver.provider}:{solver.model}@{solver.prompt_version}",
            "approve",
            {**outcome.solver.model_dump(mode="json"), "prompt_hash": solver.prompt_hash},
        ),
        ExerciseReview(
            "rubric_judge",
            f"{judge.provider}:{judge.model}@{RUBRIC_VERSION}",
            "approve",
            {
                **outcome.judge.model_dump(mode="json"),
                "rubric_version": RUBRIC_VERSION,
                "prompt_version": judge.prompt_version,
                "prompt_hash": judge.prompt_hash,
            },
        ),
    ]


@dataclass(slots=True)
class _Run:
    accepted: list[AcceptedEntry] = field(default_factory=list)
    rejected: list[RejectedEntry] = field(default_factory=list)
    calls: list[CallSummary] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    stages: dict[str, StageInfo] = field(default_factory=dict)


class ExerciseFactory:
    """One generation run: the specs in, a batch out. Nothing is written to the bank here."""

    def __init__(
        self,
        author: ExerciseAuthor,
        checker: ExerciseChecker,
        *,
        known_keys: frozenset[str] = frozenset(),
        concurrency: int = 4,
    ) -> None:
        self._author = author
        self._checker = checker
        self._known = known_keys
        self._semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run(
        self,
        batch_id: str,
        requests: Sequence[tuple[ExerciseSpec, int]],
        *,
        avoid: dict[tuple[str, str], list[str]] | None = None,
    ) -> ExerciseBatch:
        state = _Run(seen=set(self._known))
        drafts = await asyncio.gather(
            *(
                self._author.draft(
                    spec,
                    count,
                    avoid=(avoid or {}).get((spec.level, spec.category), [])[:40],
                    run_id=batch_id,
                )
                for spec, count in requests
            )
        )
        pending: list[ExerciseCandidate] = []
        for (_, _), (candidates, execution, failed) in zip(requests, drafts, strict=True):
            state.calls.extend(call_summary(e) for e in [*failed, execution])
            state.stages.setdefault("author", stage_info("author", execution, self._author.prompt))
            for candidate in candidates:
                if isinstance(candidate, tuple):
                    state.rejected.append(
                        RejectedEntry(
                            candidate=None, stage="draft", issues=["malformed"], detail=candidate[1]
                        )
                    )
                    continue
                key = candidate.dedup_key()
                if key in state.seen:
                    state.rejected.append(
                        RejectedEntry(
                            candidate=candidate.model_dump(mode="json"),
                            stage="duplicate",
                            issues=["same_correct_sentence"],
                        )
                    )
                    continue
                state.seen.add(key)
                pending.append(candidate)
        await asyncio.gather(*(self._check(batch_id, c, state) for c in pending))
        for stage in ("author", "blind_solver", "rubric_judge"):
            if stage not in state.stages:
                # A run that never reached a stage still names the prompt that would have run.
                state.stages[stage] = _unused_stage(stage, self._author, self._checker, state)
        return ExerciseBatch(
            batch_id=batch_id,
            mechanical_checks_version=MECHANICAL_CHECKS_VERSION,
            author=state.stages["author"],
            solver=state.stages["blind_solver"],
            judge=state.stages["rubric_judge"],
            specs=[
                SpecInfo(
                    level=s.level,
                    category=s.category,
                    format=s.format,
                    setting_hint=s.setting_hint,
                    requested=n,
                )
                for s, n in requests
            ],
            accepted=state.accepted,
            rejected=state.rejected,
            calls=state.calls,
        )

    async def _check(self, batch_id: str, candidate: ExerciseCandidate, state: _Run) -> None:
        dumped = candidate.model_dump(mode="json")
        async with self._semaphore:
            try:
                outcome = await self._checker.check(candidate, run_id=batch_id)
            except ProviderError as exc:
                # The last failed attempt is both `exc.execution` and in `related_executions`.
                executions = {id(e): e for e in [*exc.related_executions, exc.execution]}
                state.calls.extend(
                    call_summary(e) for e in executions.values() if isinstance(e, ExecutionRecord)
                )
                state.rejected.append(
                    RejectedEntry(
                        candidate=dumped,
                        stage="error",
                        issues=["provider_error"],
                        detail={"error": str(exc)[:300]},
                    )
                )
                return
        state.calls.extend(call_summary(e) for e in outcome.executions)
        for execution in outcome.executions:
            stage = "blind_solver" if execution.operation == OP_SOLVE else "rubric_judge"
            prompt = (
                self._checker.solver_prompt
                if stage == "blind_solver"
                else self._checker.judge_prompt
            )
            if execution.status.value == "succeeded":
                state.stages.setdefault(stage, stage_info(stage, execution, prompt))
        if not outcome.accepted:
            state.rejected.append(
                RejectedEntry(
                    candidate=dumped,
                    stage=outcome.stage,  # type: ignore[arg-type]
                    issues=list(outcome.issues),
                    detail=outcome.detail,
                )
            )
            return
        exercise = LanguageExercise.model_validate_json(candidate.model_dump_json())
        assert outcome.solver is not None and outcome.judge is not None
        state.accepted.append(
            AcceptedEntry(
                exercise=dumped,
                exercise_id=exercise.exercise_id,
                solver=outcome.solver.model_dump(mode="json"),
                judge=outcome.judge.model_dump(mode="json"),
            )
        )


def _unused_stage(
    stage: str, author: ExerciseAuthor, checker: ExerciseChecker, state: _Run
) -> StageInfo:
    prompt = {
        "author": author.prompt,
        "blind_solver": checker.solver_prompt,
        "rubric_judge": checker.judge_prompt,
    }[stage]
    return StageInfo.model_validate(
        {
            "stage": stage,
            "provider": "none",
            "model": "none",
            "model_returned": None,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.content_hash,
        }
    )
