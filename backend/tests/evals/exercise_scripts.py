"""Scripted models for the exercise tests: an author and a checker, lax or strict."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel
from scripted import ScriptedProvider

from ari.application.contracts import LLMRequest, ProviderResult
from ari.application.exercise_schemas import RUBRIC_CRITERIA
from ari.application.services.exercise_bank import OP_GENERATE, OP_SOLVE
from ari.evals.exercises.dataset import ExerciseDataset
from ari.infrastructure.exercises.models import ExerciseModelsConfig

T = TypeVar("T", bound=BaseModel)

AUTHOR = ("openai", "gpt-6-luna")
CHECKER = ("anthropic", "claude-sonnet-5-5")


def live_config(*, checker: tuple[str, str] = CHECKER, config_id: str = "scripted-ex") -> Any:
    return ExerciseModelsConfig.model_validate(
        {
            "id": config_id,
            "description": "scripted",
            "provider": "live",
            "routes": {
                "author": {"provider": AUTHOR[0], "model": AUTHOR[1]},
                "checker": {"provider": checker[0], "model": checker[1]},
            },
            "prompts": {
                "author": "exercise-author-v1",
                "solver": "exercise-solver-v1",
                "judge": "exercise-judge-v1",
            },
        }
    )


class ScriptedModels:
    """An author and a checker as two scripted providers, told apart by the operation."""

    def __init__(self, *, author: Any, solver: Any, judge: Any, checker: tuple[str, str] = CHECKER):
        self._author = ScriptedProvider(author, provider=AUTHOR[0], model=AUTHOR[1])
        self._checker = ScriptedProvider(
            lambda request, payload: (
                solver(request, payload)
                if request.context.operation == OP_SOLVE
                else judge(request, payload)
            ),
            provider=checker[0],
            model=checker[1],
        )

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        target = self._author if request.context.operation == OP_GENERATE else self._checker
        return await target.generate_structured(request, response_model)


def solve_as(chosen: int | None = None, *, also: list[int] | None = None, plausible: bool = True):
    """A solver that picks `chosen` (the declared key when None) and accepts `also` as well."""

    def respond(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        index = 0 if chosen is None else chosen
        return {
            "chosen_index": index,
            "acceptable_indices": [index, *(also or [])],
            "medically_plausible": plausible,
            "comment": "scripted",
        }

    return respond


def judge_passes(failing: tuple[str, ...] = ()):
    def respond(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "verdicts": [
                {"criterion": c, "passed": c not in failing, "reason": "scripted"}
                for c in RUBRIC_CRITERIA
            ]
        }

    return respond


def oracle_for(dataset: ExerciseDataset, split: str, *, strict: bool) -> tuple[Any, Any]:
    """A solver and a judge that know the truth of every item of the split (strict) or accept all.

    The strict one reports a second acceptable option for every defective item, so the solver
    rejects it; it stands for a perfect checker, which the suite must then rate as perfect.
    """
    # The call carries the hash of the exercise it concerns (`case_hash`): the oracle looks it up.
    known = {
        i.candidate.content_hash: (i.truth, i.candidate.answer_index)
        for i in dataset.read_split(split, gate=True)  # type: ignore[arg-type]
    }

    def solver(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        truth, key = known[request.context.case_hash]
        also = [(key + 1) % len(payload["options"])] if strict and not truth.valid else []
        return {
            "chosen_index": key,
            "acceptable_indices": [key, *also],
            "medically_plausible": True,
            "comment": "oracle",
        }

    return solver, judge_passes()


__all__ = [
    "AUTHOR",
    "CHECKER",
    "ScriptedModels",
    "judge_passes",
    "live_config",
    "oracle_for",
    "solve_as",
]


def passing_gate_report() -> Any:
    """A gate report as the live workflow writes it: the perfect checker, on the holdout."""
    import asyncio

    from scripted import DATASETS, GATES, PRICING

    from ari.evals import gates as gate_rules
    from ari.evals.exercises.dataset import load_dataset
    from ari.evals.exercises.runner import ExerciseRunRequest, run_exercises
    from ari.infrastructure.exercises.models import load_prompts

    config = live_config()
    dataset = load_dataset(DATASETS, "1")
    solver, judge = oracle_for(dataset, "holdout", strict=True)
    report = asyncio.run(
        run_exercises(
            ExerciseRunRequest(
                dataset=dataset,
                split="holdout",
                gate_mode=True,
                config=config,
                config_sha256="0" * 64,
                provider=ScriptedModels(author=lambda r, p: {}, solver=solver, judge=judge),
                prompts=load_prompts(config),
                pricing=PRICING,
                repetitions=3,
                gates=gate_rules.load_gates(GATES)[0],
            )
        )
    )
    assert report.gate is not None and report.gate.status == "passed"
    return report.model_copy(
        update={
            "config": report.config.model_copy(
                update={"provider": "live", "routes": {"checker": "anthropic:claude-sonnet-5-5"}}
            )
        }
    )
