from __future__ import annotations

import asyncio
import time
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from ari.application.contracts import LLMRequest, ProviderResult
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id
from ari.infrastructure.providers.llm_routing import CONTENT_OPERATIONS

T = TypeVar("T", bound=BaseModel)


class OpenAILLMProvider:
    def __init__(
        self,
        api_key: str,
        *,
        patient_model: str,
        evaluation_model: str,
        patient_timeout_seconds: float,
        evaluation_timeout_seconds: float,
        content_model: str | None = None,
        content_timeout_seconds: float | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._patient_model = patient_model
        self._evaluation_model = evaluation_model
        self._patient_timeout_seconds = patient_timeout_seconds
        self._evaluation_timeout_seconds = evaluation_timeout_seconds
        self._content_model = content_model or evaluation_model
        self._content_timeout_seconds = content_timeout_seconds or evaluation_timeout_seconds

    def _model_and_timeout(self, operation: str) -> tuple[str, float]:
        if operation == "session_evaluation":
            return self._evaluation_model, self._evaluation_timeout_seconds
        if operation in CONTENT_OPERATIONS:
            return self._content_model, self._content_timeout_seconds
        return self._patient_model, self._patient_timeout_seconds

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        model, timeout = self._model_and_timeout(request.context.operation)
        return await self.generate(request, response_model, model=model, timeout_seconds=timeout)

    async def generate(
        self, request: LLMRequest, response_model: type[T], *, model: str, timeout_seconds: float
    ) -> ProviderResult[T]:
        """Structured generation on the model a route chose (see llm_routing)."""
        timeout = timeout_seconds
        started = time.perf_counter()
        try:
            async with asyncio.timeout(timeout):
                response = await self._client.chat.completions.parse(
                    model=model,
                    messages=list(request.messages),  # type: ignore[arg-type]
                    response_format=response_model,
                )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError("The model returned no parsed structured output")
            usage = response.usage.model_dump() if response.usage else {}
            execution = self._record(
                request,
                model,
                started,
                ExecutionStatus.SUCCEEDED,
                usage,
                response.id,
                model_returned=response.model,
            )
            return ProviderResult(value=parsed, execution=execution)
        except Exception as exc:
            execution = self._record(request, model, started, ExecutionStatus.FAILED, {}, None, exc)
            raise ProviderError("OpenAI structured generation failed", execution=execution) from exc

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
            provider="openai",
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
