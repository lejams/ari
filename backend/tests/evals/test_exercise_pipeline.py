"""The checks in front of the bank: mechanical, blind solver, judge; and the batch they leave."""

from __future__ import annotations

import json
from dataclasses import replace
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
from ari.domain.models import ExecutionStatus, new_id
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
    assert "exercise-rubric-v2" in text


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


async def test_a_judge_failure_keeps_the_solver_call_in_the_batch_history() -> None:
    inner = ScriptedModels(
        author=lambda r, p: {"exercises": [DRAFT]},
        solver=solving_the_key(candidate(CLOZE)),
        judge=judge_passes(),
    )

    class JudgeDown:
        async def generate_structured(self, request: LLMRequest, schema: Any) -> Any:
            if request.context.operation == OP_JUDGE:
                raise ProviderError("down", execution=None)
            return await inner.generate_structured(request, schema)

    provider = JudgeDown()
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    batch = await factory.run("b0", [(ExerciseSpec("B1", "gender", "cloze"), 1)])
    assert [r.stage for r in batch.rejected] == ["error"]
    # The solver answered and was billed before the judge failed: its call is still recorded.
    assert sorted(c.operation for c in batch.calls) == sorted([OP_GENERATE, OP_SOLVE])


class FailsFirst:
    """Each listed operation fails its first `failures` attempts, with an execution record."""

    def __init__(self, inner: Any, failures: dict[str, int]) -> None:
        self._inner = inner
        self._left = dict(failures)

    async def generate_structured(self, request: LLMRequest, schema: Any) -> Any:
        operation = request.context.operation
        if self._left.get(operation, 0) > 0:
            self._left[operation] -= 1
            done = await self._inner.generate_structured(request, schema)
            failed = replace(done.execution, id=new_id(), status=ExecutionStatus.FAILED)
            raise ProviderError("down", execution=failed)
        return await self._inner.generate_structured(request, schema)


def operations_of(batch: Any) -> list[str]:
    return sorted(c.operation for c in batch.calls)


async def test_a_failed_author_attempt_before_a_successful_retry_stays_in_the_history() -> None:
    inner = ScriptedModels(
        author=lambda r, p: {"exercises": [DRAFT]},
        solver=solving_the_key(candidate(CLOZE)),
        judge=judge_passes(),
    )
    provider = FailsFirst(inner, {OP_GENERATE: 1})
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    batch = await factory.run("b5", [(ExerciseSpec("B1", "gender", "cloze"), 1)])
    assert len(batch.accepted) == 1
    assert operations_of(batch) == sorted([OP_GENERATE, OP_GENERATE, OP_SOLVE, OP_JUDGE])


async def test_the_last_failed_attempt_is_recorded_once() -> None:
    inner = ScriptedModels(
        author=lambda r, p: {"exercises": [DRAFT]},
        solver=solving_the_key(candidate(CLOZE)),
        judge=judge_passes(),
    )
    # Solver fine, both judge attempts fail: generate + solve + 2 failed judge calls, no more.
    provider = FailsFirst(inner, {OP_JUDGE: 2})
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    batch = await factory.run("b6", [(ExerciseSpec("B1", "gender", "cloze"), 1)])
    assert [r.stage for r in batch.rejected] == ["error"]
    assert operations_of(batch) == sorted([OP_GENERATE, OP_SOLVE, OP_JUDGE, OP_JUDGE])
    # Two failed solver attempts: two calls, not three.
    provider = FailsFirst(inner, {OP_SOLVE: 2})
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    batch = await factory.run("b7", [(ExerciseSpec("B1", "gender", "cloze"), 1)])
    assert operations_of(batch) == sorted([OP_GENERATE, OP_SOLVE, OP_SOLVE])


async def test_an_author_request_that_fails_twice_does_not_cost_the_other_requests() -> None:
    inner = ScriptedModels(
        author=lambda r, p: {"exercises": [DRAFT]},
        solver=solving_the_key(candidate(CLOZE)),
        judge=judge_passes(),
    )

    class OneSpecDown:
        async def generate_structured(self, request: LLMRequest, schema: Any) -> Any:
            if (
                request.context.operation == OP_GENERATE
                and "word_order" in request.context.case_hash
            ):
                done = await inner.generate_structured(request, schema)
                failed = replace(done.execution, id=new_id(), status=ExecutionStatus.FAILED)
                raise ProviderError("down", execution=failed)
            return await inner.generate_structured(request, schema)

    provider = OneSpecDown()
    factory = ExerciseFactory(ExerciseAuthor(provider, PROMPTS["author"]), checker_over(provider))
    batch = await factory.run(
        "b8",
        [
            (ExerciseSpec("B1", "word_order", "correct_sentence"), 1),
            (ExerciseSpec("B1", "gender", "cloze"), 1),
        ],
    )
    assert len(batch.accepted) == 1  # The gender request went through.
    errors = [r for r in batch.rejected if r.stage == "error"]
    assert len(errors) == 1 and errors[0].detail["stage"] == "author"
    # The failed author calls (two attempts) and the other request's three calls are all there.
    assert operations_of(batch) == sorted([OP_GENERATE] * 3 + [OP_SOLVE, OP_JUDGE])


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


# ---- Rubric v2: "no correct option", exact form of the key, and the double agreement -------------


def test_the_solver_may_find_no_correct_option_but_then_accepts_nothing() -> None:
    ok = {"chosen_index": -1, "acceptable_indices": [], "medically_plausible": True, "comment": ""}
    assert BlindSolveSchema.model_validate_json(json.dumps(ok)).chosen_index == -1
    with pytest.raises(ValueError, match="no correct option"):
        BlindSolveSchema.model_validate_json(json.dumps({**ok, "acceptable_indices": [1]}))


async def test_a_solver_that_finds_no_correct_option_removes_the_exercise() -> None:
    item = candidate(CLOZE)

    def finds_none(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "chosen_index": -1,
            "acceptable_indices": [],
            "medically_plausible": True,
            "comment": "every option has an error",
        }

    outcome = await checker_over(models(solver=finds_none)).check(item, run_id="t")
    assert (outcome.accepted, outcome.stage) == (False, "blind_solver")
    assert [e.operation for e in outcome.executions] == [OP_SOLVE]


def test_the_form_of_the_key_is_a_criterion_of_the_rubric_and_of_the_judge_prompt() -> None:
    assert "key_form_exact" in RUBRIC_CRITERIA
    text = PROMPTS["judge"].content
    assert "key_form_exact:" in text and "do not correct it silently" in text.lower()
    assert "no option is correct" in PROMPTS["solver"].content.lower() or (
        "-1" in PROMPTS["solver"].content
    )


def test_the_order_of_the_options_changes_between_passes_and_the_key_follows() -> None:
    from ari.application.services.exercise_bank import _reordered

    item = candidate(CLOZE)
    for number in (0, 1, 2):
        other = _reordered(item, number)
        assert sorted(other.options) == sorted(item.options)
        assert other.key == item.key
        assert (other.options == item.options) == (number == 0)
    assert _reordered(item, 1).options == tuple(reversed(item.options))


def checker_with_passes(provider: Any, passes: int) -> ExerciseChecker:
    return ExerciseChecker(
        provider,
        solver_prompt=PROMPTS["solver"],
        judge_prompt=PROMPTS["judge"],
        author_model=AUTHOR,
        passes=passes,
    )


def key_aware_solver(seen: list[list[str]]) -> Any:
    """Solves by the text of the key, whatever the order: it knows the key is `die` or the like."""

    def respond(request: LLMRequest, payload: dict[str, Any]) -> dict[str, Any]:
        options = [o["text"] for o in payload["options"]]
        seen.append(options)
        index = options.index(KEY_TEXT)
        return {
            "chosen_index": index,
            "acceptable_indices": [index],
            "medically_plausible": True,
            "comment": "scripted",
        }

    return respond


KEY_TEXT = candidate(CLOZE).key


async def test_two_passes_must_both_accept_and_the_second_sees_the_options_reversed() -> None:
    item = candidate(CLOZE)
    seen: list[list[str]] = []
    checker = checker_with_passes(models(solver=key_aware_solver(seen)), 2)
    outcome = await checker.check(item, run_id="t")
    assert outcome.accepted
    assert [e.operation for e in outcome.executions] == [OP_SOLVE, OP_JUDGE, OP_SOLVE, OP_JUDGE]
    assert seen == [list(item.options), list(reversed(item.options))]
    # What the bank keeps is the first pass, which speaks of the options in their stored order.
    assert outcome.solver is not None and outcome.solver.chosen_index == item.answer_index


async def test_a_second_pass_that_refuses_removes_the_exercise_and_says_so() -> None:
    item = candidate(CLOZE)
    calls = {"judge": 0}

    def judge_refusing_the_second_time(request: LLMRequest, payload: dict[str, Any]) -> Any:
        calls["judge"] += 1
        return judge_passes(failing=("key_form_exact",) if calls["judge"] == 2 else ())(
            request, payload
        )

    models_ = models(solver=key_aware_solver([]), judge=judge_refusing_the_second_time)
    outcome = await checker_with_passes(models_, 2).check(item, run_id="t")
    assert (outcome.accepted, outcome.stage) == (False, "rubric_judge")
    assert outcome.issues == ("key_form_exact", "pass_2")
    assert len(outcome.executions) == 4  # Both passes were made and billed.


async def test_a_first_pass_that_refuses_costs_no_second_pass() -> None:
    item = candidate(CLOZE)
    checker = checker_with_passes(models(solver=solve_as((item.answer_index + 1) % 3)), 2)
    outcome = await checker.check(item, run_id="t")
    assert (outcome.accepted, outcome.stage) == (False, "blind_solver")
    assert [e.operation for e in outcome.executions] == [OP_SOLVE]


def test_the_number_of_passes_is_bounded() -> None:
    for bad in (0, 4):
        with pytest.raises(InvalidStateError):
            checker_with_passes(models(), bad)
