"""Exam mode on the controlled chain (ARI_EXAM_ENGINE=chain), with deterministic fakes.

Open microphone segmented by energy, the exam patient (authored clinical phrases, screened
non-clinical wording), one voice per persona, credit after playback, interruption, report.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from types import MappingProxyType
from typing import Any

import pytest
from accounts_fixtures import sign_in
from clinical_fixtures import synthetic_bundle
from conftest import publish_with_simulated_reviews
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ari.api.app import create_app
from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.application.schemas import ExamPatientResponseSchema
from ari.application.services.exam_session import (
    CHAIN_EXAM_STACK_ID,
    ExamVoices,
    SpeechSegmenter,
    screen_free_text,
)
from ari.config import PROJECT_ROOT, Settings
from ari.container import REALTIME_STACK_ID, Container, build_container, exam_persona_voices
from ari.domain.errors import InvalidStateError
from ari.domain.models import ExecutionStatus, TurnResponseState
from ari.infrastructure.providers.fake import FakeTTSProvider, fake_tone

RATE = 24_000


def chain_settings(database_url: str, **overrides: Any) -> Settings:
    options: dict[str, Any] = {
        "environment": "test",
        "provider_mode": "fake",
        "database_url": database_url,
        "prompt_directory": PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
        "exam_engine": "chain",
        **overrides,
    }
    return Settings(_env_file=None, **options)


@pytest.fixture
def chain(database_url: str) -> Iterator[Container]:
    result = build_container(chain_settings(database_url))
    publish_with_simulated_reviews(result.cases.store, synthetic_bundle())
    yield result
    result.repository.engine.dispose()


def create_exam(client: TestClient) -> dict[str, Any]:
    case = client.get("/api/cases").json()[0]
    sign_in(client)
    learner = client.post("/api/learners", json={}).json()
    response = client.post(
        "/api/sessions",
        json={
            "learner_id": learner["id"],
            "case_id": case["id"],
            "case_version": case["version"],
            "learning_mode": "exam",
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def receive(socket: Any, wanted: str) -> dict[str, Any]:
    while True:
        event = socket.receive_json()
        if event["type"] == wanted:
            return dict(event["data"])


def tone(milliseconds: int, amplitude: int = 4000) -> bytes:
    samples = RATE * milliseconds // 1000
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 220 * i / RATE)))
        for i in range(samples)
    )


def silence(milliseconds: int) -> bytes:
    return b"\x00\x00" * (RATE * milliseconds // 1000)


def ack(socket: Any, streaming: dict[str, Any], sent: dict[str, Any]) -> None:
    correlation = {
        "turn_id": streaming["turn_id"],
        "response_id": streaming["response_id"],
        "audio_stream_id": streaming["audio_stream_id"],
    }
    socket.send_json({"type": "audio.playback_started", **correlation, "last_index": 0})
    receive(socket, "turn.audio_started")
    socket.send_json(
        {"type": "audio.playback_completed", **correlation, "last_index": sent["last_index"]}
    )
    receive(socket, "turn.delivered")


# Speech detection ------------------------------------------------------------------------


def segmenter() -> SpeechSegmenter:
    return SpeechSegmenter(sample_rate=RATE, threshold=500, silence_ms=300, min_speech_ms=100)


def test_speech_starts_after_enough_voice_and_ends_after_silence() -> None:
    vad = segmenter()
    assert vad.feed(silence(500)) == []
    started = vad.feed(tone(200))
    assert [event.kind for event in started] == ["speech_started"]
    assert vad.feed(silence(200)) == []  # a pause shorter than silence_ms keeps the utterance
    assert vad.feed(tone(200)) == []
    ended = vad.feed(silence(400))
    assert [event.kind for event in ended] == ["utterance"]
    # The utterance keeps a little audio from before speech was confirmed (pre-roll).
    assert len(ended[0].audio) >= len(tone(400)) + len(silence(300))
    assert not vad.speaking


def test_a_click_shorter_than_min_speech_is_not_speech() -> None:
    vad = segmenter()
    assert vad.feed(tone(60) + silence(500)) == []


def test_frames_split_across_messages_are_reassembled() -> None:
    vad = segmenter()
    audio = tone(300) + silence(400)
    events = [
        event
        for offset in range(0, len(audio), 7)
        for event in vad.feed(audio[offset : offset + 7])
    ]
    assert [event.kind for event in events] == ["speech_started", "utterance"]


def test_a_monologue_is_cut_at_the_maximum_length() -> None:
    vad = SpeechSegmenter(
        sample_rate=RATE, threshold=500, silence_ms=300, min_speech_ms=100, max_utterance_seconds=1
    )
    events = vad.feed(tone(1500))
    # The cut utterance goes to the patient; the speech that goes on starts the next one.
    assert [event.kind for event in events] == ["speech_started", "utterance", "speech_started"]
    assert len(events[1].audio) == RATE * 2


# Patient wording -------------------------------------------------------------------------


def test_the_schema_keeps_free_text_to_non_clinical_turns() -> None:
    ok = ExamPatientResponseSchema.model_validate_json(
        json.dumps({"response_kind": "small_talk", "source_refs": [], "free_text": "Guten Tag."})
    )
    assert ok.free_text == "Guten Tag."
    for payload in (
        {"response_kind": "small_talk", "source_refs": []},
        {"response_kind": "hesitation", "source_refs": [], "free_text": "  "},
        {"response_kind": "sources", "source_refs": ["fact:fact-1"], "free_text": "Ja."},
        {"response_kind": "small_talk", "source_refs": ["fact:fact-1"], "free_text": "Ja."},
    ):
        with pytest.raises(ValidationError):
            ExamPatientResponseSchema.model_validate_json(json.dumps(payload))


def test_free_wording_with_clinical_content_is_rejected(chain: Container) -> None:
    case = chain.cases.list()[0]
    assert screen_free_text("Gern geschehen.", case) is None
    assert screen_free_text("Entschuldigung, was meinen Sie?", case) is None
    assert screen_free_text("Seit 3 Tagen.", case) == "digit"
    assert screen_free_text("Vielleicht drei.", case) == "number_word"
    assert screen_free_text("Schon eine Woche.", case) == "unit"
    assert screen_free_text("Mir tut der Kopf weh.", case) == "medical_term"
    assert screen_free_text("Das sind Kopfschmerzen.", case) == "medical_term"


def test_a_case_term_is_rejected_in_free_wording(chain: Container) -> None:
    case = chain.cases.list()[0]
    case = replace(case, terminology=(replace(case.terminology[0], german="Synkope"),))
    assert screen_free_text("Eine Synkope vielleicht?", case) == "case_term"


# Engine selection ------------------------------------------------------------------------


def test_the_flag_pins_new_exams_on_the_chain_and_training_keeps_the_pipeline(
    chain: Container,
) -> None:
    with TestClient(create_app(chain)) as client:
        exam = create_exam(client)
    assert exam["voice_stack_id"] == CHAIN_EXAM_STACK_ID
    assert exam["voice_stack_config"]["models"]["tts"] == "eleven_v4_turbo"
    stored = chain.repository.get_session(exam["id"])
    assert chain.resolve_voice_stack(stored) is chain.chain_stack


def test_the_default_engine_stays_openai_realtime(published_container: Container) -> None:
    assert published_container.settings.exam_engine == "openai_realtime"
    assert published_container.chain_stack is None
    with TestClient(create_app(published_container)) as client:
        assert create_exam(client)["voice_stack_id"] == REALTIME_STACK_ID


def test_a_chain_exam_does_not_resume_once_the_chain_is_switched_off(
    chain: Container, database_url: str
) -> None:
    with TestClient(create_app(chain)) as client:
        exam = create_exam(client)
    realtime = build_container(replace_engine(database_url))
    try:
        with pytest.raises(InvalidStateError):
            realtime.resolve_voice_stack(realtime.repository.get_session(exam["id"]))
    finally:
        realtime.repository.engine.dispose()


def replace_engine(database_url: str) -> Settings:
    return chain_settings(database_url).model_copy(update={"exam_engine": "openai_realtime"})


def test_openai_mode_requires_the_elevenlabs_key_for_the_chain(database_url: str) -> None:
    settings = chain_settings(
        database_url,
        provider_mode="openai",
        openai_api_key="k",
        stt_api_key="k",
        elevenlabs_voice_id="",
    )
    with pytest.raises(RuntimeError) as caught:
        build_container(settings)
    assert "ARI_ELEVENLABS_API_KEY" in str(caught.value)
    assert "voice_id" in str(caught.value)


# Personas --------------------------------------------------------------------------------


def test_each_persona_has_its_voice_settings_and_tags_with_a_shared_fallback(
    database_url: str,
) -> None:
    settings = chain_settings(
        database_url,
        elevenlabs_voice_id="voice-default",
        elevenlabs_exam_personas=json.dumps(
            {
                "anxious": {
                    "voice_id": "voice-anxious",
                    "tags": "[nervous]",
                    "voice_settings": {"stability": 0.3, "speed": 1.1},
                },
                "terse": {"tags": "[flat]"},
            }
        ),
    )
    voices = exam_persona_voices(settings)
    assert voices["anxious"] == {
        "voice_id": "voice-anxious",
        "tags": "[nervous]",
        "voice_settings": {"stability": 0.3, "speed": 1.1},
    }
    assert voices["terse"]["voice_id"] == "voice-default"
    assert voices["standard"] == {"voice_id": "voice-default", "tags": "", "voice_settings": {}}


@pytest.mark.parametrize(
    "personas",
    [
        {"grumpy": {"voice_id": "v"}},  # not a persona
        {"anxious": {"tags": "Ich habe Fieber."}},  # words, not tags: they would be spoken
        {"anxious": {"voice_settings": {"speed": 3}}},
        {"anxious": {"pitch": 1}},
    ],
)
def test_persona_settings_are_validated(database_url: str, personas: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        chain_settings(database_url, elevenlabs_exam_personas=json.dumps(personas))


def test_the_persona_chooses_the_voice_never_the_facts(chain: Container) -> None:
    case = chain.cases.list()[0]
    standard, anxious = FakeTTSProvider(), FakeTTSProvider()
    voices = ExamVoices(MappingProxyType({"standard": standard, "anxious": anxious}))
    assert voices.for_case(case) is standard  # scenarios without a variant are standard
    assert voices.for_case(replace(case, persona_variant="anxious")) is anxious
    assert voices.for_case(replace(case, persona_variant="terse")) is standard


# The socket ------------------------------------------------------------------------------


def test_open_microphone_turn_is_answered_heard_and_credited(chain: Container) -> None:
    with TestClient(create_app(chain)) as client:
        session_id = create_exam(client)["id"]
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            started = receive(socket, "call.started")
            assert started["interaction"] == "open_microphone"
            assert started["voice_stack_id"] == CHAIN_EXAM_STACK_ID
            # Real PCM: the fake transcriber answers it with a fixed question.
            socket.send_bytes(silence(200) + tone(400) + silence(1000))
            receive(socket, "user.speech_started")
            assert receive(socket, "user.transcript_final")["text"]
            streaming = receive(socket, "patient.audio_streaming")
            sent = receive(socket, "patient.audio_sent")
            ack(socket, streaming, sent)
            # Then a question the fake patient answers with a fact.
            socket.send_json({"type": "debug.transcript", "transcript": "Erzählen Sie von fact-1."})
            text = receive(socket, "patient.response_text")
            assert text["text"] == "Seit 1 Tagen."  # the authored phrase, rendered by the code
            streaming = receive(socket, "patient.audio_streaming")
            sent = receive(socket, "patient.audio_sent")
            turn = chain.repository.get_session(session_id).turns[1]
            assert turn.selected_fact_ids == ("fact-1",) and turn.revealed_fact_ids == ()
            ack(socket, streaming, sent)
            assert chain.repository.get_session(session_id).turns[1].revealed_fact_ids == (
                "fact-1",
            )
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
        ended = client.post(f"/api/sessions/{session_id}/end", json={}).json()
    assert ended["status"] == "completed"
    report = ended["exam_report"]
    assert report["voice_stack_id"] == CHAIN_EXAM_STACK_ID
    assert (report["turn_count"], report["heard_turns"], report["facts_heard"]) == (2, 2, 1)
    assert report["turns"][1]["facts_heard"] == 1
    assert isinstance(report["response_ms_p95"], int)
    operations = {
        (e.operation, e.status) for e in chain.repository.get_session(session_id).executions
    }
    assert ("speech_to_text", ExecutionStatus.SUCCEEDED) in operations
    assert ("patient_simulation", ExecutionStatus.SUCCEEDED) in operations
    patient_calls = [
        e for e in chain.repository.get_session(session_id).executions
        if e.operation == "patient_simulation"
    ]  # fmt: skip
    assert {e.prompt_version for e in patient_calls} == {"patient-exam-v1"}


def test_non_clinical_turns_speak_screened_wording_or_the_authored_fallback(
    chain: Container,
) -> None:
    with TestClient(create_app(chain)) as client:
        session_id = create_exam(client)["id"]
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            receive(socket, "call.started")
            socket.send_json({"type": "debug.transcript", "transcript": "Danke schön."})
            assert receive(socket, "patient.response_text")["text"] == "Gern geschehen."
            receive(socket, "turn.completed")
            # The fake model answers this one with clinical wording: it must not be spoken.
            socket.send_json({"type": "debug.transcript", "transcript": "Wollen wir plaudern?"})
            assert receive(socket, "patient.response_text")["text"] == "Ja, danke."
            receive(socket, "turn.completed")
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
    session = chain.repository.get_session(session_id)
    assert [t.patient_response_kind for t in session.turns] == ["small_talk", "small_talk"]
    assert all(t.selected_fact_ids == () for t in session.turns)
    usage = [e.usage for e in session.executions if e.operation == "patient_simulation"]
    assert [u["free_text"] for u in usage] == ["accepted", "rejected"]
    assert usage[1]["free_text_rejection"] == "unit"  # "seit", the first clinical word


class SlowTTS:
    """Many chunks with a pause between them, so the learner can speak over the patient."""

    def __init__(self) -> None:
        self.completed = 0

    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        audio = fake_tone(text)
        for _ in range(8):
            yield AudioStreamEvent("chunk", data=audio, mime_type="audio/pcm;rate=24000")
            await asyncio.sleep(0.1)
        self.completed += 1
        fake = [e async for e in FakeTTSProvider().stream(text, context)]
        yield fake[-1]


def test_speaking_over_the_patient_stops_the_answer_and_credits_nothing(
    chain: Container,
) -> None:
    slow = SlowTTS()
    services = replace(chain, exam_voices=ExamVoices(MappingProxyType({"standard": slow})))
    with TestClient(create_app(services)) as client:
        session_id = create_exam(client)["id"]
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            receive(socket, "call.started")
            socket.send_json({"type": "debug.transcript", "transcript": "Erzählen Sie von fact-1."})
            receive(socket, "patient.audio_chunk")
            socket.send_bytes(tone(300))  # the learner talks while the patient is answering
            receive(socket, "user.speech_started")
            interrupted = receive(socket, "patient.audio_interrupted")
            assert interrupted["chunks_sent"] < 8
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
        ended = client.post(f"/api/sessions/{session_id}/end", json={}).json()
    turn = chain.repository.get_session(session_id).turns[0]
    assert turn.selected_fact_ids == ("fact-1",) and turn.revealed_fact_ids == ()
    assert turn.response_state is TurnResponseState.DELIVERY_UNCONFIRMED
    # The sentence was read to its end: its call is recorded with its cost, never lost.
    assert slow.completed == 1
    tts = [
        e
        for e in chain.repository.get_session(session_id).executions
        if e.operation == "text_to_speech"
    ]
    assert len(tts) == 1
    report = ended["exam_report"]
    assert (report["heard_turns"], report["not_heard_turns"], report["facts_not_heard"]) == (
        0,
        1,
        1,
    )


def test_an_interrupted_answer_is_not_replayed(chain: Container) -> None:
    with TestClient(create_app(chain)) as client:
        session_id = create_exam(client)["id"]
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            receive(socket, "call.started")
            socket.send_json({"type": "debug.transcript", "transcript": "Guten Tag."})
            turn = receive(socket, "turn.completed")["turn"]
            socket.send_json({"type": "turn.retry_tts", "turn_id": turn["id"]})
            receive(socket, "turn.retry_rejected")
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")


def test_microphone_frames_alone_do_not_count_as_activity(chain: Container) -> None:
    with TestClient(create_app(chain)) as client:
        session_id = create_exam(client)["id"]
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            receive(socket, "call.started")
            socket.send_bytes(silence(500))
            socket.send_json({"type": "call.end"})
            receive(socket, "call.ended")
    assert chain.repository.get_session(session_id).turns == ()


# The ElevenLabs persona levers -------------------------------------------------------------


async def test_tags_are_read_before_the_sentence_and_settings_go_with_the_voice() -> None:
    from websockets.asyncio.server import ServerConnection, serve

    from ari.infrastructure.providers.elevenlabs import ElevenLabsStreamingTTSProvider

    seen: dict[str, Any] = {}

    async def handle(ws: ServerConnection) -> None:
        seen["voices"] = json.loads(await ws.recv())["voices"]
        message = json.loads(await ws.recv())
        seen["text"] = message["inputs"][0]["text"]
        await ws.send(json.dumps({"audio": "AQI="}))
        await ws.send(json.dumps({"isFinal": True}))

    async with serve(handle, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        tts = ElevenLabsStreamingTTSProvider(
            "key",
            model="eleven_v4_turbo",
            voice_id="voice-anxious",
            timeout_seconds=2.0,
            base_url=f"ws://127.0.0.1:{port}/v1/text-to-dialogue/stream-input",
            voice_settings={"stability": 0.3},
            audio_tags="[nervous]",
        )
        context = ExecutionContext(
            session_id="s", operation="text_to_speech", case_version="1", case_hash="h"
        )
        events = [event async for event in tts.stream("Seit gestern.", context)]
    assert seen["voices"] == [{"voice_id": "voice-anxious", "voice_settings": {"stability": 0.3}}]
    assert seen["text"] == "[nervous] Seit gestern."
    execution = events[-1].execution
    assert execution is not None and execution.usage["characters"] == len("[nervous] Seit gestern.")


def test_voice_settings_need_voices_registered_as_objects() -> None:
    from ari.infrastructure.providers.elevenlabs import ElevenLabsStreamingTTSProvider

    with pytest.raises(ValueError):
        ElevenLabsStreamingTTSProvider(
            "key",
            model="eleven_v4_turbo",
            voice_id="v",
            timeout_seconds=1.0,
            voice_registration="ids",
            voice_settings={"speed": 1.1},
        )


def test_scenarios_without_a_persona_variant_keep_their_serialization() -> None:
    scenario = synthetic_bundle().scenarios[0]
    assert scenario.persona_variant is None
    assert "persona_variant" not in scenario.model_dump(mode="json")
    tagged = scenario.model_copy(update={"persona_variant": "terse"})
    assert tagged.model_dump(mode="json")["persona_variant"] == "terse"
