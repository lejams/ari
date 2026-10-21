"""Structured generation on Claude (Anthropic Messages API, `messages.parse`).

The response schema is the same Pydantic model the OpenAI adapter uses. `messages.parse`
sends it as `output_config.format`; constraints the API does not take (string lengths,
numeric bounds) are removed from the schema it sends and checked on the parsed result by the
SDK, so the model ARI receives is validated exactly as before.

A response that did not finish normally (a refusal, or the token limit) is a provider failure,
never a partial result: callers already treat a ProviderError as a technical error, which never
becomes a negative observation about the learner (plan, invariant 4). `messages.parse` validates
while it parses, so such a response usually fails there, with a validation error as the recorded
cause; the stop-reason check below covers the case where the text still parses.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any, Literal, TypeVar, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from ari.application.contracts import LLMRequest, ProviderResult
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id

T = TypeVar("T", bound=BaseModel)
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AnthropicStopError(RuntimeError):
    """Claude stopped before a complete structured answer."""


def split_messages(
    messages: Sequence[dict[str, str]],
) -> tuple[str | None, list[dict[str, str]]]:
    """System messages go to the `system` parameter; turns of the same role are joined."""
    system = [m["content"] for m in messages if m["role"] == "system"]
    turns: list[dict[str, str]] = []
    for message in messages:
        if message["role"] == "system":
            continue
        if turns and turns[-1]["role"] == message["role"]:
            turns[-1] = {"role": message["role"],
                         "content": f"{turns[-1]['content']}\n\n{message['content']}"}  # fmt: skip
        else:
            turns.append({"role": message["role"], "content": message["content"]})
    return ("\n\n".join(system) if system else None), turns


class AnthropicLLMProvider:
    def __init__(
        self,
        api_key: str,
        *,
        max_tokens: int = 16000,
        effort: Effort | None = None,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._max_tokens = max_tokens
        self._effort = effort

    async def generate(
        self, request: LLMRequest, response_model: type[T], *, model: str, timeout_seconds: float
    ) -> ProviderResult[T]:
        started = time.perf_counter()
        system, turns = split_messages(request.messages)
        options: dict[str, Any] = {}
        if system is not None:
            options["system"] = system
        if self._effort is not None:
            options["output_config"] = {"effort": self._effort}
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await self._client.messages.parse(
                    model=model,
                    max_tokens=self._max_tokens,
                    messages=cast(Any, turns),
                    output_format=response_model,
                    **options,
                )
            if response.stop_reason != "end_turn":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                raise AnthropicStopError(
                    f"stop_reason={response.stop_reason}"
                    + (f" (category {category})" if category else "")
                )
            parsed = response.parsed_output
            if parsed is None:
                raise AnthropicStopError("the model returned no parsed structured output")
            execution = self._record(
                request,
                model,
                started,
                ExecutionStatus.SUCCEEDED,
                response.usage.model_dump(mode="json"),
                response.id,
                model_returned=response.model,
            )
            return ProviderResult(value=parsed, execution=execution)
        except Exception as exc:
            execution = self._record(request, model, started, ExecutionStatus.FAILED, {}, None, exc)
            raise ProviderError(
                "Anthropic structured generation failed", execution=execution
            ) from exc

    @staticmethod
    def _record(
        request: LLMRequest,
        model: str,
        started: float,
        status: ExecutionStatus,
        usage: dict[str, object],
        request_id: str | None,
        error: Exception | None = None,
        model_returned: str | None = None,
    ) -> ExecutionRecord:
        context = request.context
        return ExecutionRecord(
            id=new_id(),
            session_id=context.session_id,
            turn_id=context.turn_id,
            operation=context.operation,
            provider="anthropic",
            model=model,
            status=status,
            prompt_version=context.prompt_version,
            prompt_hash=context.prompt_hash,
            case_version=context.case_version,
            case_hash=context.case_hash,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
            provider_request_id=request_id,
            error_code=type(error).__name__ if error else None,
            error_message=str(error)[:1000] if error else None,
            retryable=error is not None,
            model_returned=model_returned,
        )
