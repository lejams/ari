"""The evaluator votes: the language errors a majority of independent passes report stand."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from ari.application.contracts import LLMRequest, ProviderResult
from ari.application.prompting import load_prompt
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.config import PROJECT_ROOT
from ari.container import Container, build_container
from ari.domain.errors import ProviderError
from ari.domain.models import CEFRLevel, ConversationSession, ExecutionStatus
from ari.infrastructure.providers.fake import FakeLLMProvider

T = TypeVar("T", bound=BaseModel)
SPOKEN = "Nennen Sie mich bitte Ihren vollständigen Namen"
PROMPT = load_prompt(
    PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "evaluation_v10.txt", "evaluation-v10"
)


def seen_error(quote: str = "Nennen Sie mich", category: str = "case") -> dict[str, Any]:
    return {
        "text": "Explication.",
        "evidence_turn_sequences": [1],
        "quote": quote,
        "correction": "Nennen Sie mir",
        "category": category,
    }


class PassScript(FakeLLMProvider):
    """The fake evaluator, except each pass (`request.sample`) answers what the script says."""

    def __init__(
        self,
        errors: Callable[[int], list[dict[str, Any]]],
        failing: frozenset[int] = frozenset(),
    ) -> None:
        super().__init__()
        self._errors = errors
        self._failing = failing
        self.samples: list[int] = []

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        result = await super().generate_structured(request, response_model)
        self.samples.append(request.sample)
        if request.sample in self._failing:
            raise ProviderError(
                "down",
                execution=replace(result.execution, status=ExecutionStatus.FAILED),
            )
        payload = result.value.model_dump()
        payload["language_errors"] = self._errors(request.sample)
        return ProviderResult(response_model.model_validate(payload), result.execution)


@pytest.fixture
def session_and_case(published_container: Container) -> Any:
    container = published_container
    learner = container.orchestrator.create_learner(CEFRLevel.C1)
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(learner.id, case.id, case.version)
    container.orchestrator.activate(session.id)
    return session, case, container


async def evaluate(provider: PassScript, session_and_case: Any, passes: int) -> Any:
    session, case, container = session_and_case
    await container.orchestrator.process_transcript(session.id, SPOKEN)
    stored: ConversationSession = container.repository.get_session(session.id)
    return await LLMBackedEvaluator(provider, PROMPT, "fr-FR", passes=passes).evaluate(
        stored, container.cases.get(case.id, case.version)
    )


@pytest.mark.asyncio
async def test_an_error_reported_by_two_passes_out_of_three_stands(session_and_case: Any) -> None:
    provider = PassScript(lambda sample: [seen_error()] if sample < 2 else [])
    outcome = await evaluate(provider, session_and_case, passes=3)
    assert sorted(provider.samples) == [0, 1, 2]
    assert [e.text.split(" : ")[0] for e in outcome.evaluation.language_errors] == [
        "« Nennen Sie mich » → « Nennen Sie mir »"
    ]
    # The two other passes are kept for the cost.
    assert len(outcome.extra_executions) == 2


@pytest.mark.asyncio
async def test_an_error_reported_by_one_pass_out_of_three_does_not(session_and_case: Any) -> None:
    provider = PassScript(lambda sample: [seen_error()] if sample == 0 else [])
    outcome = await evaluate(provider, session_and_case, passes=3)
    assert outcome.evaluation.language_errors == ()


@pytest.mark.asyncio
async def test_a_failed_pass_is_dropped_but_its_execution_is_kept(session_and_case: Any) -> None:
    provider = PassScript(lambda sample: [seen_error()], failing=frozenset({1}))
    outcome = await evaluate(provider, session_and_case, passes=3)
    assert len(outcome.evaluation.language_errors) == 1
    statuses = sorted(e.status.value for e in outcome.extra_executions)
    assert len(statuses) == 2 and ExecutionStatus.FAILED.value in statuses


@pytest.mark.asyncio
async def test_with_two_passes_left_both_must_agree(session_and_case: Any) -> None:
    provider = PassScript(
        lambda sample: [seen_error()] if sample == 0 else [], failing=frozenset({2})
    )
    outcome = await evaluate(provider, session_and_case, passes=3)
    assert outcome.evaluation.language_errors == ()


@pytest.mark.asyncio
async def test_when_every_pass_fails_the_error_carries_all_executions(
    session_and_case: Any,
) -> None:
    provider = PassScript(lambda sample: [], failing=frozenset({0, 1, 2}))
    with pytest.raises(ProviderError) as raised:
        await evaluate(provider, session_and_case, passes=3)
    assert len(raised.value.related_executions) == 2


@pytest.mark.asyncio
async def test_one_pass_is_one_call_and_keeps_the_single_answer(session_and_case: Any) -> None:
    provider = PassScript(lambda sample: [seen_error()])
    outcome = await evaluate(provider, session_and_case, passes=1)
    assert provider.samples == [0]
    assert len(outcome.evaluation.language_errors) == 1
    assert outcome.extra_executions == ()


def test_the_number_of_passes_is_bounded() -> None:
    for passes in (0, 6):
        with pytest.raises(ValueError, match="passes"):
            LLMBackedEvaluator(FakeLLMProvider(), PROMPT, "fr-FR", passes=passes)


@pytest.mark.asyncio
async def test_every_pass_of_a_real_analysis_is_recorded_for_its_cost(
    published_container: Container,
) -> None:
    container = build_container(
        published_container.settings.model_copy(update={"evaluation_passes": 3})
    )
    try:
        learner = container.orchestrator.create_learner(CEFRLevel.C1)
        case = container.cases.list()[0]
        session = container.orchestrator.create_session(learner.id, case.id, case.version)
        container.orchestrator.activate(session.id)
        await container.orchestrator.process_transcript(session.id, SPOKEN)
        await container.orchestrator.end_session(session.id)
        stored = container.repository.get_session(session.id)
        calls = [e for e in stored.executions if e.operation == "session_evaluation"]
        assert len(calls) == 3 and len({e.id for e in calls}) == 3
        assert stored.evaluation is not None
    finally:
        container.repository.engine.dispose()
