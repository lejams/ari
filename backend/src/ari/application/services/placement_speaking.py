"""Rating of one spoken placement task: a level, and the errors the code could verify.

Shared by the placement service and by the `placement` evaluation suite, so what is measured is
what runs. "The model proposes, the code decides": every quoted error is checked against the
transcript (`verify_language_errors`) and dropped when the quote is not there, and the level is
read by the code from the model's criterion scores (`spoken_level`), averaged over the passes.
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
from ari.domain.placement import SPOKEN_CRITERIA, spoken_level

SPOKEN_TURN = 1  # The transcript of a spoken task is the single turn the error quotes cite.
MAX_PASSES = 5
# How many passes must report an error for it to stand. One: the oral keeps an error the model
# sees in any pass. The model reports what it is sure of and every quote is verified against the
# transcript, so a false report is rare, whereas a real but subtle error (a collocation, a
# register) is seen in about one pass out of two; a majority vote then drops it or keeps it by
# chance, and the same answer is rated differently from one run to the next (measured on the dev
# split of placement@1 on 5 November 2026: stability 0.37 with a majority).
SPOKEN_ERROR_VOTES = 1


@dataclass(frozen=True, slots=True)
class SpokenRating:
    estimated_level: str  # Decided by the code from `level_score` (`spoken_level`).
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
    # Mean of the criteria, then of the passes (1 = A1 ... 6 = C2), and each criterion's mean.
    level_score: float = 0.0
    criteria: dict[str, float] | None = None


def _consolidated(verdicts: list[LanguageErrorVerdict], votes: int) -> list[LanguageErrorSchema]:
    if len(verdicts) == 1:
        return list(verdicts[0].kept)
    return list(
        consolidate_language_errors([placed_errors(verdict) for verdict in verdicts], votes)
    )


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
    error_votes: int = SPOKEN_ERROR_VOTES,
) -> SpokenRating:
    """Rate one spoken task, with `passes` independent calls when the rating must be repeatable.

    One call flips between reporting an error and missing it, and a whole level read at once
    flips between two neighbouring levels, so the same answer does not always get the same
    rating. The code decides: an error stands when `error_votes` passes report it on the same
    words (one: any pass that sees it); the model scores five criteria, and the level is read on
    a fixed grid from their mean over every pass that answered (`spoken_level`). The confidence
    is the mean of the passes; the observations are those of the pass closest to the mean.
    """
    if not 1 <= passes <= MAX_PASSES:
        raise ValueError(f"passes must be between 1 and {MAX_PASSES}")
    if not 1 <= error_votes <= passes:
        raise ValueError(f"error_votes must be between 1 and {passes}")
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
    per_pass = [_pass_score(item.value) for item in results]
    score = sum(per_pass) / len(per_pass)
    # The pass that stands for the rating (its observations): the first one closest to the mean.
    primary = min(range(len(results)), key=lambda i: abs(per_pass[i] - score))
    return SpokenRating(
        estimated_level=spoken_level(score),
        confidence=sum(item.value.confidence for item in results) / len(results),
        observations=list(results[primary].value.observations),
        language_errors=_consolidated(verdicts, min(error_votes, len(verdicts))),
        proposed_errors=sum(len(item.value.language_errors) for item in results),
        invented_errors=sum(verdict.dropped.get("not_in_turn", 0) for verdict in verdicts),
        execution=results[primary].execution,
        extra_executions=tuple(item.execution for i, item in enumerate(results) if i != primary)
        + tuple(cast(ExecutionRecord, cast(ProviderError, item).execution) for item in failures),
        level_score=score,
        criteria={
            name: sum(float(item.value.criteria.model_dump()[name]) for item in results)
            / len(results)
            for name in SPOKEN_CRITERIA
        },
    )


def _pass_score(rating: SpeakingRatingSchema) -> float:
    """Mean of one pass's criterion scores."""
    scores = rating.criteria.model_dump()
    return sum(float(scores[name]) for name in SPOKEN_CRITERIA) / len(SPOKEN_CRITERIA)
