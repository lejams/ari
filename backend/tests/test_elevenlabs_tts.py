"""ElevenLabs Text to Dialogue adapter, against a deterministic local WebSocket server.

The server speaks the protocol as documented (docs/spikes/exam-elevenlabs.md, section 2): a
voice registration first, then one `inputs` message per sentence answered by base64 audio
frames and a final frame. Real ElevenLabs has not been measured (owner's decision of
20 October 2026), so these tests pin what the adapter does with every documented shape.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.application.services.costing import cost_of
from ari.config import Settings
from ari.container import _tts_provider, _voice_stack
from ari.domain.errors import ProviderError
from ari.domain.models import ExecutionStatus
from ari.infrastructure.pricing import load_pricing
from ari.infrastructure.providers.elevenlabs import ElevenLabsStreamingTTSProvider
from ari.infrastructure.providers.openai import OpenAIStreamingTTSProvider

PRICING = Path(__file__).resolve().parents[1] / "src" / "ari" / "pricing" / "pricing-v1.yaml"
CONTEXT = ExecutionContext(
    session_id="s1", operation="text_to_speech", case_version="1", case_hash="h"
)
PCM = b"\x01\x02" * 2400  # 0.1 s of 16-bit mono at 24 kHz


@dataclass
class FakeDialogue:
    """What the server does for each sentence, and what it saw."""

    frames: int = 3
    final_key: str | None = "isFinal"
    binary_audio: bool = False
    error_on_sentence: int | None = None
    close_before_answer: set[int] = field(default_factory=set)
    connections: int = 0
    registrations: list[Any] = field(default_factory=list)
    headers: list[str | None] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)

    async def handle(self, ws: ServerConnection) -> None:
        self.connections += 1
        self.headers.append(ws.request.headers.get("xi-api-key") if ws.request else None)
        self.queries.append(ws.request.path if ws.request else "")
        self.registrations.append(json.loads(await ws.recv())["voices"])
        async for raw in ws:
            message = json.loads(raw)
            text = message["inputs"][0]["text"]
            self.sentences.append(text)
            number = len(self.sentences)
            if number in self.close_before_answer:
                await ws.close()
                return
            if number == self.error_on_sentence:
                await ws.send(json.dumps({"error": "voice_not_found", "message": "unknown voice"}))
                continue
            for _ in range(self.frames):
                if self.binary_audio:
                    await ws.send(PCM)
                else:
                    await ws.send(json.dumps({"audio": base64.b64encode(PCM).decode()}))
            if self.final_key:
                await ws.send(json.dumps({self.final_key: True}))


@pytest.fixture
async def dialogue() -> AsyncIterator[tuple[FakeDialogue, str]]:
    fake = FakeDialogue()
    async with serve(fake.handle, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield fake, f"ws://127.0.0.1:{port}/v1/text-to-dialogue/stream-input"


def provider(url: str, **kw: Any) -> ElevenLabsStreamingTTSProvider:
    options: dict[str, Any] = {"timeout_seconds": 2.0, "idle_end_seconds": 0.2, **kw}
    return ElevenLabsStreamingTTSProvider(
        "test-key", model="eleven_v4_turbo", voice_id="voice-anna", base_url=url, **options
    )


async def speak(tts: ElevenLabsStreamingTTSProvider, text: str) -> list[AudioStreamEvent]:
    return [event async for event in tts.stream(text, CONTEXT)]


async def test_a_sentence_streams_pcm_and_records_a_costed_execution(
    dialogue: tuple[FakeDialogue, str],
) -> None:
    fake, url = dialogue
    events = await speak(provider(url), "Guten Tag, Herr Doktor.")

    chunks = [e for e in events if e.type == "chunk"]
    assert [e.data for e in chunks] == [PCM, PCM, PCM]
    assert {e.mime_type for e in events} == {"audio/pcm;rate=24000"}
    completed = events[-1]
    assert completed.type == "completed" and completed.execution is not None
    execution = completed.execution
    assert (execution.provider, execution.model, execution.status) == (
        "elevenlabs", "eleven_v4_turbo", ExecutionStatus.SUCCEEDED,
    )  # fmt: skip
    assert execution.usage["characters"] == len("Guten Tag, Herr Doktor.")
    assert execution.usage["audio_seconds"] == pytest.approx(0.3)
    assert isinstance(execution.usage["first_audio_ms"], int)
    # Priced against the versioned grid: 0.04 $ per 1 000 characters.
    cost = cost_of(execution, load_pricing(PRICING))
    # 23 characters x 0.04 $ / 1 000 x 0.88 EUR/USD = 809.6 micro-euros.
    assert cost.unknown_components == () and cost.cost_micro_eur in (809, 810)
    # The key travels in the header, the model and format in the query, never the key in the URL.
    assert fake.headers == ["test-key"]
    assert "model_id=eleven_v4_turbo" in fake.queries[0] and "test-key" not in fake.queries[0]
    assert "output_format=pcm_24000" in fake.queries[0]
    assert fake.registrations == [[{"voice_id": "voice-anna"}]]


async def test_a_connection_is_reused_for_the_next_sentence(
    dialogue: tuple[FakeDialogue, str],
) -> None:
    fake, url = dialogue
    tts = provider(url)
    await speak(tts, "Erste Antwort.")
    await speak(tts, "Zweite Antwort.")
    assert fake.connections == 1 and fake.sentences == ["Erste Antwort.", "Zweite Antwort."]
    await tts.close()


async def test_simultaneous_sentences_use_separate_connections_and_never_mix_audio(
    dialogue: tuple[FakeDialogue, str],
) -> None:
    fake, url = dialogue
    tts = provider(url)
    first, second = await asyncio.gather(speak(tts, "Eins."), speak(tts, "Zwei."))
    assert fake.connections == 2
    for events in (first, second):
        assert [e.data for e in events if e.type == "chunk"] == [PCM, PCM, PCM]


async def test_a_connection_closed_before_any_audio_is_replaced_once(
    dialogue: tuple[FakeDialogue, str],
) -> None:
    fake, url = dialogue
    fake.close_before_answer = {2}
    tts = provider(url)
    await speak(tts, "Erste Antwort.")
    events = await speak(tts, "Zweite Antwort.")  # pooled connection dies, a new one answers
    assert events[-1].type == "completed" and fake.connections == 2


async def test_a_service_error_fails_with_a_traceable_execution_and_drops_the_connection(
    dialogue: tuple[FakeDialogue, str],
) -> None:
    fake, url = dialogue
    fake.error_on_sentence = 1
    tts = provider(url)
    with pytest.raises(ProviderError) as caught:
        await speak(tts, "Hallo.")
    execution = caught.value.execution
    assert execution.status is ExecutionStatus.FAILED and execution.provider == "elevenlabs"
    assert "voice_not_found" in (execution.error_message or "")
    assert "test-key" not in (execution.error_message or "")
    await speak(tts, "Noch einmal.")
    assert fake.connections == 2  # the failed connection was not reused


async def test_without_a_final_frame_silence_ends_the_sentence_and_the_connection_is_dropped(
    dialogue: tuple[FakeDialogue, str],
) -> None:
    fake, url = dialogue
    fake.final_key = None
    tts = provider(url)
    events = await speak(tts, "Ja.")
    assert events[-1].type == "completed" and len(events) == 4
    await speak(tts, "Nein.")
    assert fake.connections == 2


@pytest.mark.parametrize("final_key", ["isFinal", "is_final", "final"])
async def test_every_documented_final_key_ends_a_sentence(
    dialogue: tuple[FakeDialogue, str], final_key: str
) -> None:
    fake, url = dialogue
    fake.final_key = final_key
    tts = provider(url, idle_end_seconds=5.0)  # a missed final would wait 5 s
    events = await asyncio.wait_for(speak(tts, "Ja."), 2.0)
    assert events[-1].type == "completed"


async def test_binary_audio_frames_are_accepted(dialogue: tuple[FakeDialogue, str]) -> None:
    fake, url = dialogue
    fake.binary_audio = True
    events = await speak(provider(url), "Ja.")
    assert [e.data for e in events if e.type == "chunk"] == [PCM, PCM, PCM]


async def test_a_listener_who_stops_early_never_leaves_audio_for_the_next_sentence(
    dialogue: tuple[FakeDialogue, str],
) -> None:
    fake, url = dialogue
    fake.frames = 6
    tts = provider(url)
    stream = tts.stream("Eine lange Antwort.", CONTEXT)
    first = await anext(stream)
    assert first.type == "chunk"
    await stream.aclose()  # barge-in: the learner speaks over the patient
    events = await speak(tts, "Neue Antwort.")
    assert [e.data for e in events if e.type == "chunk"] == [PCM] * 6
    assert fake.connections == 2


async def test_voice_registration_by_bare_id(dialogue: tuple[FakeDialogue, str]) -> None:
    fake, url = dialogue
    await speak(provider(url, voice_registration="ids"), "Ja.")
    assert fake.registrations == [["voice-anna"]]


async def test_a_silent_service_times_out_without_retrying() -> None:
    async def never_answers(ws: ServerConnection) -> None:
        await asyncio.sleep(10)

    async with serve(never_answers, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        tts = provider(f"ws://127.0.0.1:{port}/", timeout_seconds=0.3)
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(ProviderError):
            await speak(tts, "Hallo.")
        assert loop.time() - started < 0.9  # one wait, not two


def test_only_the_24_khz_pcm_the_players_expect_is_accepted() -> None:
    # The training player takes audio/pcm;rate=24000 only and the placement oral wraps at 24 kHz.
    for unsupported in ("mp3_44100_128", "pcm_16000", "pcm_22050"):
        with pytest.raises(ValueError, match="24 kHz"):
            provider("ws://unused", output_format=unsupported)
    assert provider("ws://unused")._mime_type == "audio/pcm;rate=24000"


async def test_a_service_that_never_stops_streaming_fails_at_the_overall_deadline() -> None:
    async def endless(ws: ServerConnection) -> None:
        await ws.recv()  # voice registration
        await ws.recv()  # the sentence
        while True:  # audio forever, never a final frame, never a pause
            await ws.send(json.dumps({"audio": base64.b64encode(PCM).decode()}))
            await asyncio.sleep(0.05)

    async with serve(endless, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        tts = provider(f"ws://127.0.0.1:{port}/", timeout_seconds=0.5, idle_end_seconds=0.2)
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(ProviderError) as caught:
            await speak(tts, "Hallo.")
        assert loop.time() - started < 1.0
        assert caught.value.execution.status is ExecutionStatus.FAILED


# ----- configuration --------------------------------------------------------------------------


def test_openai_stays_the_default_voice_and_its_stack_snapshot_is_unchanged() -> None:
    settings = Settings(provider_mode="openai", openai_api_key="k", stt_api_key="k")
    assert isinstance(_tts_provider(settings), OpenAIStreamingTTSProvider)
    stack = _voice_stack(settings)
    assert stack.models["tts"] == "gpt-4o-mini-tts"
    assert set(stack.parameters) == {"sample_rate", "stt_base_url", "tts_voice"}


def test_elevenlabs_is_chosen_by_setting_and_pinned_on_the_session_stack() -> None:
    settings = Settings(
        provider_mode="openai",
        openai_api_key="k",
        stt_api_key="k",
        tts_provider="elevenlabs",
        elevenlabs_api_key="e",
        elevenlabs_voice_id="voice-anna",
    )
    assert isinstance(_tts_provider(settings), ElevenLabsStreamingTTSProvider)
    stack = _voice_stack(settings)
    assert stack.models["tts"] == "eleven_v4_turbo"
    assert stack.parameters["tts_provider"] == "elevenlabs"
    assert stack.parameters["tts_voice"] == "voice-anna"
    assert stack.parameters["tts_output_format"] == "pcm_24000"
