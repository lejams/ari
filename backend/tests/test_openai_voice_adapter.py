from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import BadRequestError
from openai.types.realtime import RealtimeTranscriptionSessionCreateRequestParam

from ari.application.contracts import ExecutionContext, TranscriptionConfig
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionStatus
from ari.infrastructure.providers.fake import FakeTTSProvider
from ari.infrastructure.providers.openai import stt as stt_module
from ari.infrastructure.providers.openai.stt import (
    OpenAIStreamingSTTConnection,
    OpenAIStreamingSTTProvider,
)


class FakeRealtimeSocket:
    def __init__(
        self,
        handshake: list[dict[str, object]],
        events: list[dict[str, object]] | None = None,
    ) -> None:
        self.handshake = [json.dumps(item) for item in handshake]
        self.events = [json.dumps(item) for item in events or []]
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return self.handshake.pop(0)

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if not self.events:
            raise StopAsyncIteration
        return self.events.pop(0)

    async def close(self) -> None:
        self.closed = True


def context() -> ExecutionContext:
    return ExecutionContext(
        session_id="session-1",
        operation="speech_to_text",
        case_version="1.0.0",
        case_hash="case-hash",
    )


def provider() -> OpenAIStreamingSTTProvider:
    return OpenAIStreamingSTTProvider(
        "test-key",
        model="gpt-4o-mini-transcribe",
        realtime_url="wss://api.openai.com/v1/realtime",
        sample_rate=24_000,
        silence_ms=700,
        prefix_padding_ms=300,
        vad_threshold=0.5,
        handshake_timeout_seconds=1,
    )


def test_realtime_transcription_rejects_unsupported_sample_rate() -> None:
    with pytest.raises(ValueError, match="24000 Hz"):
        OpenAIStreamingSTTProvider(
            "test-key",
            model="gpt-4o-mini-transcribe",
            realtime_url="wss://api.openai.com/v1/realtime",
            sample_rate=16_000,
            silence_ms=700,
            prefix_padding_ms=300,
            vad_threshold=0.5,
            handshake_timeout_seconds=1,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(("locale", "expected"), [("en-US", "en"), ("de-DE", "de")])
async def test_realtime_transcription_payload_uses_case_language(
    monkeypatch: pytest.MonkeyPatch, locale: str, expected: str
) -> None:
    socket = FakeRealtimeSocket([{"type": "session.created"}])

    connection_arguments: dict[str, object] = {}
    secret_arguments: dict[str, object] = {}

    async def fake_connect(url: str, **kwargs: object) -> Any:
        connection_arguments.update(url=url, **kwargs)
        return socket

    async def fake_create_client_secret(
        self: object, session: RealtimeTranscriptionSessionCreateRequestParam
    ) -> str:
        del self
        secret_arguments["session"] = session
        return "ephemeral-test-key"

    monkeypatch.setattr(stt_module, "connect", fake_connect)
    monkeypatch.setattr(
        OpenAIStreamingSTTProvider, "_create_client_secret", fake_create_client_secret
    )
    config = TranscriptionConfig(
        expected_locales=(locale,),
        context_prompt="Medical interview",
        prompt_version="stt-context:test@1",
        prompt_hash="prompt-hash",
    )

    connection = await provider().connect(context(), config)
    assert connection_arguments["url"] == "wss://api.openai.com/v1/realtime"
    assert connection_arguments["additional_headers"] == {
        "Authorization": "Bearer ephemeral-test-key"
    }
    session = secret_arguments["session"]
    assert isinstance(session, dict)
    transcription = session["audio"]["input"]["transcription"]

    assert transcription == {
        "model": "gpt-4o-mini-transcribe",
        "language": expected,
        "prompt": "Medical interview",
    }
    assert session["audio"]["input"]["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 700,
    }
    assert session["audio"]["input"]["noise_reduction"] == {"type": "near_field"}
    await connection.close()
    assert socket.closed is True


@pytest.mark.asyncio
async def test_realtime_handshake_error_is_traceable(monkeypatch: pytest.MonkeyPatch) -> None:
    socket = FakeRealtimeSocket(
        [{"type": "error", "error": {"code": "invalid_request", "message": "bad config"}}]
    )

    async def fake_connect(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        return socket

    async def fake_create_client_secret(
        self: object, session: RealtimeTranscriptionSessionCreateRequestParam
    ) -> str:
        del self, session
        return "ephemeral-test-key"

    monkeypatch.setattr(stt_module, "connect", fake_connect)
    monkeypatch.setattr(
        OpenAIStreamingSTTProvider, "_create_client_secret", fake_create_client_secret
    )
    config = TranscriptionConfig(
        expected_locales=("en-US",),
        context_prompt="Medical interview",
        prompt_version="stt-context:test@1",
        prompt_hash="prompt-hash",
    )

    with pytest.raises(ProviderError) as captured:
        await provider().connect(context(), config)

    execution = captured.value.execution
    assert execution.status is ExecutionStatus.FAILED
    assert execution.prompt_version == "stt-context:test@1"
    assert execution.prompt_hash == "prompt-hash"
    assert execution.error_code == "invalid_request"
    assert "bad config" in (execution.error_message or "")
    assert socket.closed is True


@pytest.mark.asyncio
async def test_client_secret_api_error_is_traceable(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/realtime/client_secrets")
    response = httpx.Response(
        400,
        request=request,
        headers={"x-request-id": "request-123"},
    )

    async def fake_create_client_secret(
        self: object, session: RealtimeTranscriptionSessionCreateRequestParam
    ) -> str:
        del self, session
        raise BadRequestError(
            "unsupported model",
            response=response,
            body={"code": "invalid_model", "type": "invalid_request_error"},
        )

    async def unexpected_connect(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        raise AssertionError("WebSocket must not open when client-secret creation fails")

    monkeypatch.setattr(
        OpenAIStreamingSTTProvider, "_create_client_secret", fake_create_client_secret
    )
    monkeypatch.setattr(stt_module, "connect", unexpected_connect)
    config = TranscriptionConfig(
        expected_locales=("en-US",),
        context_prompt="Medical interview",
        prompt_version="stt-context:test@1",
        prompt_hash="prompt-hash",
    )

    with pytest.raises(ProviderError) as captured:
        await provider().connect(context(), config)

    execution = captured.value.execution
    assert execution.error_code == "invalid_model"
    assert execution.provider_request_id == "request-123"
    assert execution.latency_ms >= 0
    assert execution.retryable is False


@pytest.mark.asyncio
async def test_fake_tts_emits_raw_24khz_pcm() -> None:
    events = [event async for event in FakeTTSProvider().stream("Hello", context())]
    chunks = [event for event in events if event.type == "chunk"]

    assert chunks
    assert all(event.mime_type == "audio/pcm;rate=24000" for event in chunks)
    assert all(event.data is not None and len(event.data) % 2 == 0 for event in chunks)
    assert events[-1].execution is not None


@pytest.mark.asyncio
async def test_realtime_completions_are_reconciled_by_item_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = FakeRealtimeSocket(
        [{"type": "session.created"}],
        [
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item-1",
                "audio_start_ms": 100,
            },
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "item-1",
                "audio_end_ms": 1600,
            },
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item-2",
                "audio_start_ms": 1700,
            },
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "item-2",
                "audio_end_ms": 2200,
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-2",
                "transcript": "second",
            },
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-1",
                "transcript": "first",
            },
        ],
    )

    async def fake_connect(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        return socket

    async def fake_create_client_secret(
        self: object, session: RealtimeTranscriptionSessionCreateRequestParam
    ) -> str:
        del self, session
        return "ephemeral-test-key"

    monkeypatch.setattr(stt_module, "connect", fake_connect)
    monkeypatch.setattr(
        OpenAIStreamingSTTProvider, "_create_client_secret", fake_create_client_secret
    )
    config = TranscriptionConfig(
        expected_locales=("en-US",),
        context_prompt="Medical interview",
        prompt_version="stt-context:test@1",
        prompt_hash="prompt-hash",
    )
    connection = await provider().connect(context(), config)
    events = [event async for event in connection.events()]
    finals = [event for event in events if event.type == "transcript_final"]

    assert [(event.item_id, event.text) for event in finals] == [
        ("item-1", "first"),
        ("item-2", "second"),
    ]
    assert [event.execution.usage["audio_seconds"] for event in finals if event.execution] == [
        1.5,
        0.5,
    ]


@pytest.mark.asyncio
async def test_unsegmented_pcm_does_not_fabricate_billable_stt_duration() -> None:
    socket = FakeRealtimeSocket([], [{
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "item-1", "transcript": "Frage",
    }])
    connection = OpenAIStreamingSTTConnection(
        socket, context(), TranscriptionConfig(("de-DE",), "test", "v1", "hash"),
        "gpt-transcribe", 24_000,
    )
    await connection.send_audio(bytes(48_000))
    events = [event async for event in connection.events()]
    execution = events[-1].execution
    assert execution is not None
    assert execution.usage["observed_pcm_bytes"] == 48_000
    assert execution.usage["audio_seconds"] is None


@pytest.mark.asyncio
async def test_stt_endpoint_latency_excludes_transcript_reordering_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr(stt_module, "time", SimpleNamespace(perf_counter=lambda: clock[0]))

    class TimedSocket(FakeRealtimeSocket):
        async def __anext__(self) -> str:
            message = await super().__anext__()
            clock[0] = json.loads(message)["observed_time"]
            return message

    socket = TimedSocket([], [
        {"type": "input_audio_buffer.speech_started", "item_id": "a", "observed_time": 1},
        {"type": "input_audio_buffer.speech_stopped", "item_id": "a", "observed_time": 2},
        {"type": "input_audio_buffer.speech_started", "item_id": "b", "observed_time": 3},
        {"type": "input_audio_buffer.speech_stopped", "item_id": "b", "observed_time": 4},
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "b",
         "transcript": "second", "observed_time": 4.25},
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "a",
         "transcript": "first", "observed_time": 5},
    ])
    connection = OpenAIStreamingSTTConnection(
        socket, context(), TranscriptionConfig(("de-DE",), "test", "v1", "hash"),
        "gpt-transcribe", 24_000,
    )
    finals = [event async for event in connection.events() if event.type == "transcript_final"]
    assert [event.execution.usage["speech_end_to_transcript_final_ms"]
            for event in finals if event.execution] == [3000, 250]
