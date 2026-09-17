"""Whisper transcription through any OpenAI-compatible /audio/transcriptions endpoint."""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from typing import Any
from urllib.parse import urlparse

from openai import AsyncOpenAI

from ari.application.contracts import ExecutionContext, TranscriptionConfig
from ari.application.ports.stt import Transcription
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionRecord, ExecutionStatus, new_id

logger = logging.getLogger(__name__)


def wav_bytes(pcm16: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as container:
        container.setnchannels(1)
        container.setsampwidth(2)
        container.setframerate(sample_rate)
        container.writeframes(pcm16)
    return buffer.getvalue()


def primary_language(locale: str) -> str:
    return locale.split("-")[0].lower()


class WhisperTranscriber:
    def __init__(self, api_key: str, *, base_url: str, model: str, timeout_seconds: float) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._provider = urlparse(base_url).hostname or "whisper"
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def transcribe(
        self,
        pcm16: bytes,
        *,
        sample_rate: int,
        context: ExecutionContext,
        config: TranscriptionConfig,
    ) -> Transcription:
        started = time.perf_counter()
        usage: dict[str, object] = {"audio_seconds": round(len(pcm16) / (sample_rate * 2), 3)}
        options: dict[str, Any] = {"response_format": "json", "temperature": 0}
        if config.expected_locales:
            options["language"] = primary_language(config.expected_locales[0])
        if config.context_prompt:
            options["prompt"] = config.context_prompt
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._client.audio.transcriptions.create(
                    model=self._model,
                    file=("turn.wav", wav_bytes(pcm16, sample_rate), "audio/wav"),
                    **options,
                )
        except Exception as exc:
            execution = self._record(context, config, started, ExecutionStatus.FAILED, usage, exc)
            # The API answer stays generic; the console tells the developer what happened.
            logger.warning(
                "Whisper transcription failed (%s, %.1fs of audio, model %s): %s",
                self._provider,
                usage["audio_seconds"],
                self._model,
                str(exc)[:300],
            )
            raise ProviderError("Whisper transcription failed", execution=execution) from exc
        text = response.text.strip()
        usage["characters"] = len(text)
        return Transcription(
            text, self._record(context, config, started, ExecutionStatus.SUCCEEDED, usage)
        )

    def _record(
        self,
        context: ExecutionContext,
        config: TranscriptionConfig,
        started: float,
        status: ExecutionStatus,
        usage: dict[str, object],
        error: Exception | None = None,
    ) -> ExecutionRecord:
        return ExecutionRecord(
            id=new_id(),
            session_id=context.session_id,
            turn_id=context.turn_id,
            operation="speech_to_text",
            provider=self._provider,
            model=self._model,
            status=status,
            prompt_version=config.prompt_version,
            prompt_hash=config.prompt_hash,
            case_version=context.case_version,
            case_hash=context.case_hash,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=usage,
            provider_request_id=getattr(error, "request_id", None) if error else None,
            error_code=type(error).__name__ if error else None,
            error_message=str(error)[:1000] if error else None,
            retryable=error is not None,
        )
