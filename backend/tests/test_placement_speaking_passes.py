"""The placement oral is rated by passes: a majority decides the errors, the median the level."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from ari.application.contracts import ExecutionContext, LLMRequest, ProviderResult
from ari.application.prompting import load_prompt
from ari.application.services.placement_speaking import rate_spoken_task
from ari.config import PROJECT_ROOT
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionStatus
from ari.infrastructure.providers.fake import FakeLLMProvider

T = TypeVar("T", bound=BaseModel)
TRANSCRIPT = "Ich spreche jeden Tag mit meinen Kollegen. Besonders hilfsbereit finde ich Gespräche."
PROMPT = load_prompt(
    PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "placement_speaking_v2.txt",
    "placement-speaking-v2",
)
CONTEXT = ExecutionContext(
    session_id="s",
    operation="placement_speaking",
    case_version="1",
    case_hash="c",
    prompt_version="v",
    prompt_hash="h",
)


def slip(quote: str, correction: str, category: str = "word_choice") -> dict[str, Any]:
    return {
        "text": "Erklärung.",
        "evidence_turn_sequences": [1],
        "quote": quote,
        "correction": correction,
        "category": category,
    }


HILFSBEREIT = slip("hilfsbereit", "hilfreich")
SPRECHE = slip("Ich spreche jeden Tag", "Ich unterhalte mich jeden Tag")


class PassScript(FakeLLMProvider):
    """The fake rater, except each pass (`request.sample`) answers what the script says."""

    def __init__(
        self,
        script: Callable[[int], tuple[str, list[dict[str, Any]]]],
        failing: frozenset[int] = frozenset(),
    ) -> None:
        super().__init__()
        self._script = script
        self._failing = failing
        self.samples: list[int] = []

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        result = await super().generate_structured(request, response_model)
        self.samples.append(request.sample)
        if request.sample in self._failing:
            raise ProviderError(
                "down", execution=replace(result.execution, status=ExecutionStatus.FAILED)
            )
        level, errors = self._script(request.sample)
        payload = result.value.model_dump()
        payload.update(estimated_level=level, language_errors=errors, confidence=0.8)
        return ProviderResult(response_model.model_validate(payload), result.execution)


async def rate(provider: PassScript, passes: int) -> Any:
    return await rate_spoken_task(
        provider,
        PROMPT,
        test_language="de-DE",
        feedback_language="fr-FR",
        task="Erzählen Sie.",
        task_level_hint="B2",
        target_seconds=60,
        transcript=TRANSCRIPT,
        context=CONTEXT,
        passes=passes,
    )


async def test_an_error_one_pass_misses_still_stands_when_two_see_it() -> None:
    provider = PassScript(lambda s: ("B2", [HILFSBEREIT, SPRECHE] if s != 1 else [SPRECHE]))
    rating = await rate(provider, 3)
    assert {e.quote for e in rating.language_errors} == {"hilfsbereit", "Ich spreche jeden Tag"}
    assert sorted(provider.samples) == [0, 1, 2]


async def test_an_error_only_one_pass_reports_is_dropped() -> None:
    provider = PassScript(lambda s: ("B2", [HILFSBEREIT] if s == 2 else []))
    rating = await rate(provider, 3)
    assert rating.language_errors == []
    assert rating.proposed_errors == 1  # What was proposed is still counted, for the measure.


async def test_the_level_is_the_median_of_the_passes() -> None:
    levels = {0: "B1", 1: "A2", 2: "B1"}
    rating = await rate(PassScript(lambda s: (levels[s], [])), 3)
    assert rating.estimated_level == "B1"
    assert rating.confidence == pytest.approx(0.8)
    spread = {0: "C1", 1: "B2", 2: "B1"}
    assert (await rate(PassScript(lambda s: (spread[s], [])), 3)).estimated_level == "B2"


async def test_the_lower_level_stands_when_an_even_number_of_passes_remain() -> None:
    levels = {0: "B2", 1: "B1", 2: "B2"}
    provider = PassScript(lambda s: (levels[s], []), failing=frozenset({2}))
    rating = await rate(provider, 3)
    assert rating.estimated_level == "B1"


async def test_a_failed_pass_is_recorded_and_the_others_still_decide() -> None:
    provider = PassScript(lambda s: ("B2", [HILFSBEREIT]), failing=frozenset({1}))
    rating = await rate(provider, 3)
    assert [e.quote for e in rating.language_errors] == ["hilfsbereit"]
    assert len(rating.extra_executions) == 2
    assert {e.status for e in rating.extra_executions} == {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
    }


async def test_every_pass_failing_raises_with_all_the_executions() -> None:
    provider = PassScript(lambda s: ("B2", []), failing=frozenset({0, 1, 2}))
    with pytest.raises(ProviderError) as raised:
        await rate(provider, 3)
    assert len(raised.value.related_executions) == 2


async def test_a_single_pass_is_the_plain_rating() -> None:
    provider = PassScript(lambda s: ("B1", [HILFSBEREIT]))
    rating = await rate(provider, 1)
    assert rating.estimated_level == "B1" and len(rating.language_errors) == 1
    assert rating.extra_executions == () and provider.samples == [0]


async def test_the_number_of_passes_is_bounded() -> None:
    for passes in (0, 6):
        with pytest.raises(ValueError):
            await rate(PassScript(lambda s: ("B1", [])), passes)
