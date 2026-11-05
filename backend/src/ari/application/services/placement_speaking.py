"""Rating of one spoken placement task: a level, and the errors the code could verify.

Shared by the placement service and by the `placement` evaluation suite, so what is measured is
what runs. "The model proposes, the code decides": every quoted error is checked against the
transcript (`verify_language_errors`) and dropped when the quote is not there, and with several
passes the errors and the level are the ones the passes agree on.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import cast

from ari.application.contracts import ExecutionContext, LLMRequest, ProviderResult
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import LanguageErrorSchema, SpeakingRatingSchema
from ari.application.services.language_errors import (
    LanguageErrorVerdict,
    consolidate_language_errors,
    placed_errors,
    verify_language_errors,
)
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord
from ari.domain.placement import LEVELS

SPOKEN_TURN = 1  # The transcript of a spoken task is the single turn the error quotes cite.
MAX_PASSES = 5


@dataclass(frozen=True, slots=True)
class SpokenRating:
    estimated_level: str
    confidence: float
    observations: list[str]
    language_errors: list[LanguageErrorSchema]  # Verified: every quote stands in the transcript.
    proposed_errors: int  # What the model proposed (all passes), before the verification.
    # Quotes that stand nowhere in the transcript: invented evidence. A repeated or a
    # transcription-only report is dropped too, but its quote exists, so it is not counted here.
    invented_errors: int
    execution: ExecutionRecord
    # The other passes' calls, failed ones included: they cost money and are recorded too.
    extra_executions: tuple[ExecutionRecord, ...] = ()


def _consolidated(verdicts: list[LanguageErrorVerdict]) -> list[LanguageErrorSchema]:
    if len(verdicts) == 1:
        return list(verdicts[0].kept)
    return list(consolidate_language_errors([placed_errors(verdict) for verdict in verdicts]))


async def rate_spoken_task(
    llm: LLMProvider,
    prompt: VersionedPrompt,
    *,
    test_language: str,
    feedback_language: str,
    task: str,
    task_level_hint: str,
    target_seconds: int,
    transcript: str,
    context: ExecutionContext,
    passes: int = 1,
) -> SpokenRating:
    """Rate one spoken task, with `passes` independent calls when the rating must be repeatable.

    One call flips between reporting an error and missing it, and between two neighbouring
    levels, so the same answer does not always get the same rating. With several passes the code
    decides: an error stands when a majority of the passes report it on the same words, and the
    level is the median of the passes' levels (the lower one when an even number of passes
    remain, some having failed). The confidence is the mean of the passes that gave that level.
    """
    if not 1 <= passes <= MAX_PASSES:
        raise ValueError(f"passes must be between 1 and {MAX_PASSES}")
    messages = (
        {"role": "system", "content": prompt.content},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "test_language": test_language,
                    "feedback_language": feedback_language,
                    "task": task,
                    "task_level_hint": task_level_hint,
                    "target_seconds": target_seconds,
                    "transcript": transcript,
                },
                ensure_ascii=False,
            ),
        },
    )

    async def run_pass(sample: int) -> ProviderResult[SpeakingRatingSchema]:
        request = LLMRequest(messages=messages, context=context, sample=sample)
        return await llm.generate_structured(request, SpeakingRatingSchema)

    outcomes = await asyncio.gather(
        *(run_pass(sample) for sample in range(passes)), return_exceptions=True
    )
    results = [item for item in outcomes if isinstance(item, ProviderResult)]
    failures = [item for item in outcomes if isinstance(item, BaseException)]
    for failure in failures:
        if not isinstance(failure, ProviderError):
            raise failure
    if not results:
        first = cast(ProviderError, failures[0])
        first.related_executions = tuple(
            cast(ProviderError, item).execution for item in failures[1:]
        )
        raise first
    verdicts = [
        verify_language_errors(item.value.language_errors, {SPOKEN_TURN: transcript})
        for item in results
    ]
    levels = sorted((item.value.estimated_level for item in results), key=LEVELS.index)
    level = levels[(len(levels) - 1) // 2]
    # The pass that stands for the rating: the first one that gave the consolidated level.
    primary = next(i for i, item in enumerate(results) if item.value.estimated_level == level)
    agreeing = [item.value.confidence for item in results if item.value.estimated_level == level]
    return SpokenRating(
        estimated_level=level,
        confidence=sum(agreeing) / len(agreeing),
        observations=list(results[primary].value.observations),
        language_errors=_consolidated(verdicts),
        proposed_errors=sum(len(item.value.language_errors) for item in results),
        invented_errors=sum(verdict.dropped.get("not_in_turn", 0) for verdict in verdicts),
        execution=results[primary].execution,
        extra_executions=tuple(item.execution for i, item in enumerate(results) if i != primary)
        + tuple(cast(ExecutionRecord, cast(ProviderError, item).execution) for item in failures),
    )
