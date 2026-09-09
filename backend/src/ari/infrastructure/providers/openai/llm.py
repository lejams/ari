from __future__ import annotations

import asyncio
import time
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from ari.application.contracts import LLMRequest, ProviderResult
from ari.domain.errors import ProviderError
from ari.domain.models import CostStatus, ExecutionRecord, ExecutionStatus, new_id
from ari.infrastructure.providers.openai.pricing import (
    PRICING_VERSION,
    CostResult,
    calculate_llm_cost,
)

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
        grounding_model: str | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._patient_model = patient_model
        self._grounding_model = grounding_model or patient_model
        self._evaluation_model = evaluation_model
        self._patient_timeout_seconds = patient_timeout_seconds
        self._evaluation_timeout_seconds = evaluation_timeout_seconds

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        if request.context.operation == "session_evaluation":
            model = self._evaluation_model
        elif request.context.operation == "grounding_audit":
            model = self._grounding_model
        else:
            model = self._patient_model
        started = time.perf_counter()
        try:
            timeout = (
                self._evaluation_timeout_seconds
                if request.context.operation == "session_evaluation"
                else self._patient_timeout_seconds
            )
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
                cost=calculate_llm_cost(model, usage),
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
        cost: CostResult | None = None,
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
            estimated_cost_usd=cost.amount_usd if cost is not None else None,
            pricing_version=cost.pricing_version if cost is not None else PRICING_VERSION,
            cost_status=cost.status if cost is not None else CostStatus.UNKNOWN,
            cost_amount_usd=cost.amount_usd if cost is not None else None,
            cost_units=dict(cost.units) if cost is not None else {},
            cost_assumptions=cost.assumptions if cost is not None else (),
            cost_unknown_reason=(
                cost.unknown_reason
                if cost is not None
                else "Provider call failed before billable usage was available"
            ),
            provider_request_id=request_id,
            error_code=type(error).__name__ if error else None,
            error_message=str(error)[:1000] if error else None,
            retryable=error is not None,
        )
