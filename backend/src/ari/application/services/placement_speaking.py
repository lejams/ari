"""Rating of one spoken placement task: a level, and the errors the code could verify.

Shared by the placement service and by the `placement` evaluation suite, so what is measured is
what runs. "The model proposes, the code decides": every quoted error is checked against the
transcript (`verify_language_errors`) and dropped when the quote is not there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.ports.llm import LLMProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import LanguageErrorSchema, SpeakingRatingSchema
from ari.application.services.language_errors import verify_language_errors
from ari.domain.models import ExecutionRecord

SPOKEN_TURN = 1  # The transcript of a spoken task is the single turn the error quotes cite.


@dataclass(frozen=True, slots=True)
class SpokenRating:
    estimated_level: str
    confidence: float
    observations: list[str]
    language_errors: list[LanguageErrorSchema]  # Verified: every quote stands in the transcript.
    proposed_errors: int  # What the model proposed, before the verification.
    # Quotes that stand nowhere in the transcript: invented evidence. A repeated or a
    # transcription-only report is dropped too, but its quote exists, so it is not counted here.
    invented_errors: int
    execution: ExecutionRecord


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
) -> SpokenRating:
    request = LLMRequest(
        messages=(
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
        ),
        context=context,
    )
    result = await llm.generate_structured(request, SpeakingRatingSchema)
    verdict = verify_language_errors(result.value.language_errors, {SPOKEN_TURN: transcript})
    return SpokenRating(
        estimated_level=result.value.estimated_level,
        confidence=result.value.confidence,
        observations=list(result.value.observations),
        language_errors=list(verdict.kept),
        proposed_errors=len(result.value.language_errors),
        invented_errors=verdict.dropped.get("not_in_turn", 0),
        execution=result.execution,
    )
