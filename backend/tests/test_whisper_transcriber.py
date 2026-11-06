from __future__ import annotations

import io
import wave
from types import SimpleNamespace
from typing import Any, cast

import httpx
import openai
import pytest

from ari.application.contracts import ExecutionContext, TranscriptionConfig
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionStatus
from ari.infrastructure.providers.whisper import WhisperTranscriber, primary_language, wav_bytes

CONTEXT = ExecutionContext(
    session_id="s", operation="speech_to_text", case_version="1", case_hash="h"
)
CONFIG = TranscriptionConfig(("de-DE",), "Anamnese in der Notaufnahme", "stt-context:c@1", "hash")


class _Transcriptions:
    def __init__(self, text: str | None) -> None:
        self.text, self.calls = text, []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.text is None:
            raise RuntimeError("upstream unavailable")
        return SimpleNamespace(text=self.text)


def _transcriber(text: str | None) -> tuple[WhisperTranscriber, _Transcriptions]:
    transcriber = WhisperTranscriber(
        "key",
        base_url="https://api.groq.com/openai/v1",
        model="whisper-large-v3",
        timeout_seconds=1,
    )
    calls = _Transcriptions(text)
    transcriber._client = cast(Any, SimpleNamespace(audio=SimpleNamespace(transcriptions=calls)))
    return transcriber, calls


def test_pcm_is_wrapped_as_mono_wav_and_language_is_derived() -> None:
    header = wav_bytes(bytes(4800), 24_000)
    with wave.open(io.BytesIO(header)) as container:
        assert (container.getnchannels(), container.getsampwidth(), container.getframerate()) == (
            1,
            2,
            24_000,
        )
        assert container.getnframes() == 2400
    assert primary_language("de-DE") == "de"


@pytest.mark.asyncio
async def test_transcription_sends_case_context_and_records_execution() -> None:
    transcriber, calls = _transcriber("  Seit gestern. ")
    result = await transcriber.transcribe(
        bytes(48_000), sample_rate=24_000, context=CONTEXT, config=CONFIG
    )
    assert result.text == "Seit gestern."
    call = calls.calls[0]
    assert call["model"] == "whisper-large-v3"
    assert call["language"] == "de"
    assert call["prompt"] == "Anamnese in der Notaufnahme"
    assert call["file"][0] == "turn.wav" and call["file"][1][:4] == b"RIFF"
    execution = result.execution
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.provider == "api.groq.com"
    assert execution.prompt_version == "stt-context:c@1"
    assert execution.usage == {"audio_seconds": 1.0, "characters": 13}


@pytest.mark.asyncio
async def test_provider_failure_is_traceable() -> None:
    transcriber, _ = _transcriber(None)
    with pytest.raises(ProviderError) as captured:
        await transcriber.transcribe(
            bytes(4800), sample_rate=24_000, context=CONTEXT, config=CONFIG
        )
    execution = captured.value.execution
    assert execution is not None
    assert execution.status is ExecutionStatus.FAILED
    assert execution.error_code == "RuntimeError"
    assert execution.retryable is True
    # The learner never reads the English technical message.
    assert captured.value.public_message == "La transcription de votre voix n'a pas abouti."
    assert "Whisper" not in captured.value.public_message


def _status_error(code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/audio/transcriptions")
    response = httpx.Response(code, request=request)
    return openai.APIStatusError("provider said no", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), "pris trop de temps"),
        (
            openai.APIConnectionError(request=httpx.Request("POST", "https://x")),
            "momentanément indisponible",
        ),
        (_status_error(429), "momentanément indisponible"),
        (_status_error(503), "momentanément indisponible"),
        (_status_error(401), "n'a pas abouti"),
        (_status_error(400), "n'a pas abouti"),
    ],
)
@pytest.mark.asyncio
async def test_learner_message_depends_on_the_kind_of_failure(
    error: Exception, expected: str
) -> None:
    transcriber, calls = _transcriber("x")

    async def fail(**_: Any) -> None:
        raise error

    calls.create = fail  # type: ignore[method-assign]
    with pytest.raises(ProviderError) as captured:
        await transcriber.transcribe(
            bytes(4800), sample_rate=24_000, context=CONTEXT, config=CONFIG
        )
    assert captured.value.public_message is not None
    assert expected in captured.value.public_message
    assert str(captured.value) == "Whisper transcription failed"
