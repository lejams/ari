from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id
from ari.infrastructure.providers.openai.pricing import calculate_tts_cost


class OpenAIStreamingTTSProvider:
    def __init__(
        self, api_key: str, *, model: str, voice: str = "coral", timeout_seconds: float
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._voice = voice
        self._timeout_seconds = timeout_seconds

    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        started = time.perf_counter()
        first_audio_ms: int | None = None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.audio.speech.with_streaming_response.create(
                    model=self._model,
                    voice=self._voice,
                    input=text,
                    response_format="pcm",
                ) as response:
                    async for chunk in response.iter_bytes(chunk_size=4 * 1024):
                        if first_audio_ms is None:
                            first_audio_ms = int((time.perf_counter() - started) * 1000)
                        yield AudioStreamEvent(
                            "chunk", data=chunk, mime_type="audio/pcm;rate=24000"
                        )
            execution = self._record(
                context,
                started,
                ExecutionStatus.SUCCEEDED,
                len(text),
                first_audio_ms=first_audio_ms,
            )
            yield AudioStreamEvent(
                "completed", mime_type="audio/pcm;rate=24000", execution=execution
            )
        except Exception as exc:
            execution = self._record(context, started, ExecutionStatus.FAILED, len(text), exc)
            raise ProviderError("OpenAI speech synthesis failed", execution=execution) from exc

    def _record(
        self,
        context: ExecutionContext,
        started: float,
        status: ExecutionStatus,
        characters: int,
        error: Exception | None = None,
        first_audio_ms: int | None = None,
    ) -> ExecutionRecord:
        usage: dict[str, object] = {
            "characters": characters,
            "first_audio_ms": first_audio_ms,
        }
        cost = calculate_tts_cost(self._model, usage)
        return ExecutionRecord(
            id=new_id(),
            session_id=context.session_id,
            turn_id=context.turn_id,
            operation="text_to_speech",
            provider="openai",
            model=self._model,
            status=status,
            prompt_version=None,
            prompt_hash=None,
            case_version=context.case_version,
            case_hash=context.case_hash,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
            estimated_cost_usd=cost.amount_usd,
            pricing_version=cost.pricing_version,
            cost_status=cost.status,
            cost_amount_usd=cost.amount_usd,
            cost_units=dict(cost.units),
            cost_assumptions=cost.assumptions,
            cost_unknown_reason=cost.unknown_reason,
            error_code=type(error).__name__ if error else None,
            error_message=str(error)[:1000] if error else None,
            retryable=error is not None,
        )
