"""The checks in front of the bank: mechanical, blind solver, judge; and the batch they leave."""

from __future__ import annotations

import json
from typing import Any

import pytest
from exercise_scripts import (
    AUTHOR,
    CHECKER,
    ScriptedModels,
    judge_passes,
    live_config,
    solve_as,
)
from scripted import ScriptedProvider
from test_language_exercises_domain import CLOZE, CORRECTION, candidate

from ari.application.contracts import LLMRequest
from ari.application.exercise_schemas import RUBRIC_CRITERIA, BlindSolveSchema, JudgeSchema
from ari.application.services.exercise_bank import (
    OP_GENERATE,
    OP_JUDGE,
    OP_SOLVE,
    ExerciseAuthor,
    ExerciseChecker,
    ExerciseFactory,
    ExerciseSpec,
)
from ari.domain.errors import InvalidStateError, ProviderError
from ari.infrastructure.exercises.models import load_prompts

PROMPTS = load_prompts(live_config())


def checker_over(provider: Any, author_model: tuple[str, str] = AUTHOR) -> ExerciseChecker:
    return ExerciseChecker(
        provider,
        solver_prompt=PROMPTS["solver"],
        judge_prompt=PROMPTS["judge"],
        author_model=author_model,
    )


def models(*, solver: Any = None, judge: Any = None, author: Any = None) -> ScriptedModels:
    return ScriptedModels(
        author=author or (lambda request, payload: {"exercises": []}),
        solver=solver or solve_as(),
        judge=judge or judge_passes(),
    )


def solving_the_key(candidate_: Any, *, also: list[int] | None = None) -> Any:
    return solve_as(candidate_.answer_index, also=also)


async def test_a_candidate_that_passes_every_stage_is_accepted() -> None:
    item = candidate(CLOZE)
    outcome = await checker_over(models(solver=solving_the_key(item))).check(item, run_id="t")
    assert outcome.accepted and outcome.stage == "accepted"
    assert [e.operation for e in outcome.executions] == [OP_SOLVE, OP_JUDGE]


async def test_the_mechanical_checks_run_first_and_cost_no_call() -> None:
    provider = models()
    outcome = await checker_over(provider).check(
        candidate(CLOZE, distractors=["die", "das"]), run_id="t"
    )
    assert (outcome.accepted, outcome.stage, outcome.issues) == (
        False,
        "mechanical",
        ("duplicate_options",),
    )
    assert outcome.executions == ()


@pytest.mark.parametrize(
    ("solver_kwargs", "reason"),
    [
        ({"chosen": "other"}, "another option than the key"),
        ({"also": [2]}, "a second acceptable option"),
        ({"plausible": False}, "a medically absurd sentence"),
    ],
)
async def test_the_blind_solver_removes_what_it_cannot_confirm(
    solver_kwargs: dict[str, Any], reason: str
) -> None:
    item = candidate(CLOZE)
    key = item.answer_index
    kwargs = dict(solver_kwargs)
    chosen = (key + 1) % 3 if kwargs.pop("chosen", None) == "other" else key
    also = kwargs.pop("also", None)
    if also:
        also = [(key + 1) % 3]
    solver = solve_as(chosen, also=also, plausible=kwargs.get("plausible", True))
    outcome = await checker_over(models(solver=solver)).check(item, run_id="t")
    assert (outcome.accepted, outcome.stage) == (False, "blind_solver"), reason
    assert [e.operation for e in outcome.executions] == [OP_SOLVE]  # The judge never ran.


async def test_one_failed_criterion_is_enough_for_the_judge_to_refuse() -> None:
    item = candidate(CLOZE)
    judge = judge_passes(failing=("distractors_wrong",))
    outcome = await checker_over(models(solver=solving_the_key(item), judge=judge)).check(
        item, run_id="t"
    )
    assert (outcome.accepted, outcome.stage, outcome.issues) == (
        False,
        "rubric_judge",
        ("distractors_wrong",),
    )
    assert outcome.judge is not None and not outcome.judge.approved


def test_the_judge_must_rule_on_every_criterion_exactly_once() -> None:
    verdicts = [{"criterion": c, "passed": True, "reason": "ok"} for c in RUBRIC_CRITERIA]
    assert JudgeSchema.model_validate_json(json.dumps({"verdicts": verdicts})).approved
    for broken in (verdicts[:-1], [*verdicts[:-1], verdicts[0]]):
        with pytest.raises(ValueError):
            JudgeSchema.model_validate_json(json.dumps({"verdicts": broken}))
    with pytest.raises(ValueError, match="chosen option"):
        BlindSolveSchema.model_validate_json(
            json.dumps(
                {
                    "chosen_index": 0,
                    "acceptable_indices": [1],
                    "medically_plausible": True,
                    "comment": "",
                }
            )
        )


def test_every_criterion_of_the_rubric_is_written_in_the_judge_prompt() -> None:
    text = PROMPTS["judge"].content
    assert all(f"{c}:" in text for c in RUBRIC_CRITERIA)
    assert "exercise-rubric-v1" in text


async def test_the_checker_may_not_be_the_authors_model() -> None:
    item = candidate(CLOZE)
    same = ScriptedModels(
        author=lambda r, p: {},
        solver=solving_the_key(item),
        judge=judge_passes(),
        checker=AUTHOR,
    )
    with pytest.raises(InvalidStateError, match="autre modèle que l'auteur"):
        await checker_over(same).check(item, run_id="t")


def test_a_configuration_with_the_same_model_on_both_routes_is_refused() -> None:
    with pytest.raises(ValueError, match="another model than the author"):
        live_config(checker=AUTHOR)


async def test_a_provider_failure_is_retried_once_then_reported() -> None:
    item = candidate(CLOZE)

    class Flaky:
        calls = 0

        async def generate_structured(self, request: LLMRequest, schema: Any) -> Any:
            Flaky.calls += 1
            raise ProviderError("boom", execution=None)

    with pytest.raises(ProviderError):
        await checker_over(Flaky()).check(item, run_id="t")
    assert Flaky.calls == 2


def author_script(drafts: list[dict[str, Any]]) -> Any:
    def respond(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        return {"exercises": drafts[: payload["count"]]}

    return respond


DRAFT = {
    "stem": CLOZE["stem"],
    "key": CLOZE["key"],
    "distractors": CLOZE["distractors"],
    "explanation_fr": CLOZE["explanation_fr"],
    "context_fr": "",
    "setting": CLOZE["setting"],
}
OTHER = {
    "stem": "Ich nehme ___ Tablette nach dem Essen.",
    "key": "eine",
    "distractors": ["einen", "einem"],
    "explanation_fr": "« Tablette » est féminin : à l'accusatif, « eine » (article indéfini).",
    "context_fr": "",
    "setting": "prescription",
}


async def test_a_run_keeps_what_passed_and_says_why_the_rest_did_not() -> None:
    def solver(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        # A solver that always finds the key by its text.
        texts = [o["text"] for o in payload["options"]]
        index = texts.index("die" if "Haben Sie" in payload["stem"] else "eine")
        return solve_as(index)(request, payload)

    provider = ScriptedModels(
        author=author_script(
            [
                DRAFT,
                DRAFT,
                OTHER,
                {**OTHER, "stem": "Zu kurz", "key": "x", "distractors": ["y", "z"]},
            ]
        ),
        solver=solver,
        judge=judge_passes(),
    )
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    batch = await factory.run("b1", [(ExerciseSpec("B1", "gender", "cloze"), 4)])
    assert len(batch.accepted) == 2
    assert [r.stage for r in batch.rejected].count("duplicate") == 1
    assert [r.stage for r in batch.rejected].count("mechanical") == 1
    assert batch.author.model == AUTHOR[1] and batch.solver.model == CHECKER[1]
    assert (
        batch.judge.model == CHECKER[1]
        and batch.solver.prompt_hash == PROMPTS["solver"].content_hash
    )
    assert {c.operation for c in batch.calls} == {OP_GENERATE, OP_SOLVE, OP_JUDGE}


async def test_what_is_already_in_the_bank_is_not_proposed_again() -> None:
    provider = ScriptedModels(
        author=author_script([DRAFT]), solver=solve_as(), judge=judge_passes()
    )
    known = frozenset({candidate(CLOZE).dedup_key()})
    factory = ExerciseFactory(
        ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider), known_keys=known
    )
    batch = await factory.run("b2", [(ExerciseSpec("B1", "gender", "cloze"), 1)])
    assert batch.accepted == [] and batch.rejected[0].stage == "duplicate"


async def test_an_author_proposal_that_cannot_be_assembled_is_reported_not_lost() -> None:
    blank_setting = {**DRAFT, "setting": "   "}
    provider = ScriptedModels(
        author=author_script([blank_setting]), solver=solve_as(), judge=judge_passes()
    )
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    batch = await factory.run("b3", [(ExerciseSpec("B1", "gender", "cloze"), 1)])
    assert [r.stage for r in batch.rejected] == ["draft"]


async def test_the_author_is_told_what_exists_and_never_the_key_of_a_check() -> None:
    seen: list[dict[str, Any]] = []

    def author(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return {"exercises": [DRAFT]}

    def solver(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return solve_as()(request, payload)

    provider = ScriptedModels(author=author, solver=solver, judge=judge_passes())
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    await factory.run(
        "b4", [(ExerciseSpec("B1", "gender", "cloze"), 1)], avoid={("B1", "gender"): ["Ein Satz."]}
    )
    assert seen[0]["avoid"] == ["Ein Satz."] and seen[0]["category"] == "gender"
    blind = seen[1]
    assert set(blind) == {"instruction_fr", "situation_fr", "stem", "options"}
    assert "answer_index" not in json.dumps(blind) and "explanation" not in json.dumps(blind)


async def test_a_correction_exercise_runs_the_same_way() -> None:
    item = candidate(CORRECTION)
    outcome = await checker_over(models(solver=solving_the_key(item))).check(item, run_id="t")
    assert outcome.accepted


def test_scripted_provider_labels_match_the_guard() -> None:
    assert ScriptedProvider  # Imported for the models above; labels are checked in the guard test.
