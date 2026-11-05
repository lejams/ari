"""The placement oral is rated by passes: any pass keeps an error, the mean score sets the level."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from ari.application.contracts import ExecutionContext, LLMRequest, ProviderResult
from ari.application.prompting import load_prompt
from ari.application.schemas import SpeakingCriteriaSchema
from ari.application.services.placement_speaking import rate_spoken_task
from ari.config import PROJECT_ROOT
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionStatus
from ari.domain.placement import LEVELS, SPOKEN_CRITERIA, spoken_level
from ari.infrastructure.providers.fake import FakeLLMProvider

T = TypeVar("T", bound=BaseModel)
TRANSCRIPT = "Ich spreche jeden Tag mit meinen Kollegen. Besonders hilfsbereit finde ich Gespräche."
PROMPT = load_prompt(
    PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "placement_speaking_v3.txt",
    "placement-speaking-v3",
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
        script: Callable[[int], tuple[str | float, list[dict[str, Any]]]],
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
        # A level stands for every criterion at that level's score; a number is the score itself.
        score = level if isinstance(level, float) else float(LEVELS.index(level) + 1)
        payload = result.value.model_dump()
        payload.update(
            criteria=dict.fromkeys(SPOKEN_CRITERIA, score), language_errors=errors, confidence=0.8
        )
        return ProviderResult(response_model.model_validate(payload), result.execution)


async def rate(provider: PassScript, passes: int, **extra: Any) -> Any:
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
        **extra,
    )


async def test_an_error_one_pass_misses_still_stands() -> None:
    provider = PassScript(lambda s: ("B2", [HILFSBEREIT, SPRECHE] if s != 1 else [SPRECHE]))
    rating = await rate(provider, 3)
    assert {e.quote for e in rating.language_errors} == {"hilfsbereit", "Ich spreche jeden Tag"}
    assert sorted(provider.samples) == [0, 1, 2]


async def test_an_error_only_one_pass_reports_stands_by_default() -> None:
    provider = PassScript(lambda s: ("B2", [HILFSBEREIT] if s == 2 else []))
    rating = await rate(provider, 3)
    assert [e.quote for e in rating.language_errors] == ["hilfsbereit"]
    assert rating.proposed_errors == 1


async def test_a_majority_can_be_asked_for() -> None:
    provider = PassScript(lambda s: ("B2", [HILFSBEREIT] if s == 2 else []))
    assert (await rate(provider, 3, error_votes=2)).language_errors == []
    both = PassScript(lambda s: ("B2", [HILFSBEREIT] if s != 0 else []))
    assert len((await rate(both, 3, error_votes=2)).language_errors) == 1


async def test_an_invented_quote_never_stands_whatever_the_votes() -> None:
    invented = slip("dieser Satz wurde nie gesagt", "dieser Satz wurde nie gesagt, ja")
    provider = PassScript(lambda s: ("B2", [invented] if s == 0 else []))
    rating = await rate(provider, 3)
    assert rating.language_errors == [] and rating.invented_errors == 1


async def test_passes_that_name_one_error_differently_give_one_error() -> None:
    wide = slip("Besonders hilfsbereit finde ich", "Besonders hilfreich finde ich")
    provider = PassScript(lambda s: ("B2", [HILFSBEREIT] if s == 0 else [wide]))
    rating = await rate(provider, 3)
    assert len(rating.language_errors) == 1


async def test_the_level_is_read_by_the_code_from_the_mean_score_of_the_passes() -> None:
    levels: dict[int, str | float] = {0: "B1", 1: "A2", 2: "B1"}  # 3, 2, 3: mean 2.67
    rating = await rate(PassScript(lambda s: (levels[s], [])), 3)
    assert rating.estimated_level == "B1" and rating.level_score == pytest.approx(8 / 3)
    assert rating.confidence == pytest.approx(0.8)
    assert rating.criteria == dict.fromkeys(SPOKEN_CRITERIA, pytest.approx(8 / 3))
    spread: dict[int, str | float] = {0: "C1", 1: "B2", 2: "B1"}
    assert (await rate(PassScript(lambda s: (spread[s], [])), 3)).estimated_level == "B2"


def test_the_grid_reads_a_level_from_a_mean_score() -> None:
    assert [spoken_level(x) for x in (1.0, 1.49, 1.5, 2.5, 3.49, 3.5, 4.5, 5.5, 6.0)] == [
        "A1",
        "A1",
        "A2",
        "B1",
        "B1",
        "B2",
        "C1",
        "C2",
        "C2",
    ]
    for outside in (0.9, 6.1):
        with pytest.raises(ValueError):
            spoken_level(outside)
    # The criteria the code averages are exactly the schema's.
    assert tuple(SpeakingCriteriaSchema.model_fields) == SPOKEN_CRITERIA


async def test_a_failed_pass_does_not_count_in_the_level() -> None:
    levels: dict[int, str | float] = {0: "B2", 1: "B1", 2: "A2"}
    rating = await rate(PassScript(lambda s: (levels[s], [])), 3)
    assert rating.estimated_level == "B1"  # 4, 3, 2: mean 3
    provider = PassScript(lambda s: (levels[s], []), failing=frozenset({2}))
    rating = await rate(provider, 3)
    assert rating.estimated_level == "B2"  # 4, 3: mean 3.5, the floor of B2


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
    for votes in (0, 4):
        with pytest.raises(ValueError):
            await rate(PassScript(lambda s: ("B1", [])), 3, error_votes=votes)
