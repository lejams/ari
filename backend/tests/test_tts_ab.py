"""A/B test of the training voice: OpenAI (gpt-4o-mini-tts) against ElevenLabs (eleven_v4_turbo).

ARI_TTS_PROVIDER=ab draws one voice per new training session and pins it on the session, so a
resumed session keeps its voice. An administrator can force one voice per account from the CLI.
The exam keeps its own stack. Without an ElevenLabs key the draw is off, explicitly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from accounts_fixtures import sign_in
from clinical_fixtures import synthetic_bundle
from conftest import PROJECT_ROOT, publish_with_simulated_reviews
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect
from test_execution_costs import _execution

from ari.api.app import create_app
from ari.application.contracts import AudioStreamEvent, ExecutionContext
from ari.application.voice_stacks import TrainingVoices, VoiceStack
from ari.backoffice_api import cli
from ari.config import Settings
from ari.container import Container, _training_voices, build_container
from ari.domain.errors import InvalidStateError, NotFoundError, ProviderError
from ari.domain.models import (
    ConversationSession,
    ExecutionStatus,
    LearnerProfile,
    LearningGoal,
    LearningMode,
    new_id,
)
from ari.infrastructure.persistence.costs import CostReport
from ari.infrastructure.persistence.platform.engine import create_platform_engine
from ari.infrastructure.persistence.platform.repository import ExecutionRow
from ari.infrastructure.persistence.platform.voice_providers import VoiceProviders
from ari.infrastructure.providers.fake import FakeTTSProvider


@pytest.fixture(autouse=True)
def _loggers_enabled() -> None:
    # Alembic's logging setup disables loggers created before the first migration of the run.
    for name in ("ari.api.voice_socket", "ari.container", "ari.application.voice_stacks"):
        logging.getLogger(name).disabled = False


def _with_voices(container: Container, openai: Any, elevenlabs: Any) -> Container:
    """The container whose default voice (`tts`) and per-provider voices are these spies."""
    return replace(
        container, tts=openai, tts_providers={"openai": openai, "elevenlabs": elevenlabs}
    )


# ----- the chooser, without a database ----------------------------------------------------


def _stack(provider: str) -> VoiceStack:
    parameters: dict[str, object] = {"sample_rate": 24_000}
    if provider == "elevenlabs":
        parameters["tts_provider"] = "elevenlabs"
    return VoiceStack("pipeline_economy", "1", "fake", {"tts": provider}, parameters)


def test_a_forced_voice_wins_and_the_draw_is_even_otherwise() -> None:
    stacks = {"openai": _stack("openai"), "elevenlabs": _stack("elevenlabs")}
    voices = TrainingVoices(stacks, default=None)
    assert voices.choose("openai") is stacks["openai"]
    assert voices.choose("elevenlabs") is stacks["elevenlabs"]
    drawn = [voices.choose().models["tts"] for _ in range(200)]
    assert set(drawn) == {"openai", "elevenlabs"}
    assert 60 < drawn.count("openai") < 140  # a fair coin: 200 draws, never anywhere near 0 or 200


def test_a_default_voice_is_given_to_everyone_but_a_forced_account() -> None:
    stacks = {"openai": _stack("openai"), "elevenlabs": _stack("elevenlabs")}
    voices = TrainingVoices(stacks, default="openai")
    assert {voices.choose().models["tts"] for _ in range(50)} == {"openai"}
    assert voices.choose("elevenlabs") is stacks["elevenlabs"]


def test_a_forced_voice_that_is_not_configured_falls_back_to_the_default_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    voices = TrainingVoices({"openai": _stack("openai")}, default="openai")
    with caplog.at_level(logging.WARNING):
        assert voices.choose("elevenlabs").models["tts"] == "openai"
    assert "elevenlabs" in caplog.text


def test_a_default_without_a_stack_is_refused() -> None:
    with pytest.raises(ValueError, match="no stack"):
        TrainingVoices({"openai": _stack("openai")}, default="elevenlabs")


# ----- what ARI_TTS_PROVIDER builds ---------------------------------------------------------


def _live(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        provider_mode="openai",
        openai_api_key="k",
        stt_api_key="k",
        **overrides,
    )


def test_ab_with_a_key_draws_between_both_voices() -> None:
    voices = _training_voices(_live(tts_provider="ab", elevenlabs_api_key="e"))
    assert set(voices.providers) == {"openai", "elevenlabs"}
    assert {voices.choose().parameters.get("tts_provider", "openai") for _ in range(100)} == {
        "openai",
        "elevenlabs",
    }


def test_ab_without_a_key_falls_back_to_openai_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        voices = _training_voices(_live(tts_provider="ab"))
    assert voices.providers == ("openai",)
    assert "ARI_ELEVENLABS_API_KEY" in caplog.text
    assert voices.choose("elevenlabs").models["tts"] == "gpt-4o-mini-tts"


def test_openai_stays_the_default_and_a_key_only_allows_forced_accounts() -> None:
    voices = _training_voices(_live(elevenlabs_api_key="e"))
    assert {voices.choose().models["tts"] for _ in range(50)} == {"gpt-4o-mini-tts"}
    assert voices.choose("elevenlabs").models["tts"] == "eleven_v4_turbo"


def test_the_openai_stack_snapshot_is_unchanged_by_the_ab_test() -> None:
    plain = _training_voices(_live()).default_stack.snapshot()
    ab = _training_voices(_live(tts_provider="ab", elevenlabs_api_key="e"))
    openai = next(s for s in ab.stacks if "tts_provider" not in s.parameters)
    assert openai.snapshot() == plain  # sessions created before the test still resume


# ----- sessions, over the platform database -----------------------------------------------------


@pytest.fixture
def make_container(database_url: str) -> Iterator[Any]:
    built: list[Container] = []

    def make(**overrides: Any) -> Container:
        settings = Settings(
            _env_file=None,
            environment="test",
            provider_mode="fake",
            database_url=database_url,
            prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
            **overrides,
        )
        container = build_container(settings)
        publish_with_simulated_reviews(container.cases.store, synthetic_bundle())
        built.append(container)
        return container

    yield make
    for container in built:
        container.repository.engine.dispose()


def _case(container: Container) -> tuple[str, str]:
    case = container.cases.list()[0]
    return case.id, case.version


def _new_learner(container: Container) -> LearnerProfile:
    return container.repository.create_learner(LearnerProfile(id=new_id(), goal=LearningGoal()))


def _voice_of(session: ConversationSession) -> str:
    return str(session.voice_stack_config["parameters"].get("tts_provider", "openai"))


def test_each_training_session_is_pinned_on_its_drawn_voice_and_resumes_on_it(
    make_container: Any,
) -> None:
    container = make_container(tts_provider="ab")
    learner, (case_id, version) = _new_learner(container), _case(container)
    sessions = [
        container.orchestrator.create_session(learner.id, case_id, version) for _ in range(40)
    ]
    assert {_voice_of(s) for s in sessions} == {"openai", "elevenlabs"}
    for session in sessions:
        stored = container.repository.get_session(session.id)
        stack = container.resolve_voice_stack(stored)
        assert stack.snapshot() == dict(session.voice_stack_config)
        # A session resumes on the voice it was created with, whatever the next draw gives.
        assert _voice_of(stored) == _voice_of(session)


def test_the_exam_ignores_the_ab_test(make_container: Any) -> None:
    container = make_container(tts_provider="ab")
    learner, (case_id, version) = _new_learner(container), _case(container)
    exams = [
        container.orchestrator.create_session(
            learner.id, case_id, version, learning_mode=LearningMode.EXAM
        )
        for _ in range(10)
    ]
    assert {s.voice_stack_id for s in exams} == {container.realtime_stack.id}


def test_the_cli_forces_clears_and_lists_a_voice(
    make_container: Any,
    database_url: str,
    content_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    container = make_container(tts_provider="ab")
    with TestClient(create_app(container)) as client:
        email = sign_in(client)
        learner_id = str(client.post("/api/learners", json={}).json()["id"])
    for name, value in {
        "ARI_ENVIRONMENT": "test",
        "ARI_PROVIDER_MODE": "fake",
        "ARI_DATABASE_URL": database_url,
        "ARI_CONTENT_DATABASE_URL": content_database_url,
    }.items():
        monkeypatch.setenv(name, value)

    assert cli.run(["voice-provider", "set", "--email", email, "--provider", "openai"]) == 0
    assert json.loads(capsys.readouterr().out)["tts_provider"] == "openai"
    assert container.repository.get_learner(learner_id).tts_provider == "openai"
    case_id, version = _case(container)
    assert {
        _voice_of(container.orchestrator.create_session(learner_id, case_id, version))
        for _ in range(15)
    } == {"openai"}

    assert cli.run(["voice-provider", "list"]) == 0
    assert [i["email"] for i in json.loads(capsys.readouterr().out)["items"]] == [email]

    assert cli.run(["voice-provider", "clear", "--email", email]) == 0
    assert json.loads(capsys.readouterr().out)["tts_provider"] is None
    assert container.repository.get_learner(learner_id).tts_provider is None

    assert (
        cli.run(["voice-provider", "set", "--email", "nobody@example.test", "--provider", "openai"])
        == 2
    )
    assert "NotFoundError" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.run(["voice-provider", "set", "--email", email])  # no --provider
    with pytest.raises(SystemExit):
        cli.run(["voice-provider", "set", "--email", email, "--provider", "azure"])


def test_forcing_needs_an_onboarded_account_and_a_known_voice(make_container: Any) -> None:
    container = make_container()
    providers = VoiceProviders(container.repository.engine)
    with pytest.raises(NotFoundError):
        providers.force("nobody@example.test", "openai")
    with TestClient(create_app(container)) as client:
        early = sign_in(client)
        with pytest.raises(InvalidStateError, match="onboarding"):
            providers.force(early, "openai")
        client.post("/api/learners", json={})
        with pytest.raises(InvalidStateError, match="Voix inconnue"):
            providers.force(early, "azure")
        assert providers.force(early, "elevenlabs").tts_provider == "elevenlabs"
        assert providers.force(early, "elevenlabs").tts_provider == "elevenlabs"  # idempotent


# ----- the voice socket speaks with the voice the session was pinned with ---------------------


class TaggedTTS:
    """The fake voice, recorded as another provider; optionally failing before any audio."""

    def __init__(self, provider: str, model: str, *, fail: bool = False) -> None:
        self.provider, self.model, self.fail = provider, model, fail
        self.spoken: list[str] = []

    async def stream(self, text: str, context: ExecutionContext) -> AsyncIterator[AudioStreamEvent]:
        self.spoken.append(text)
        async for event in FakeTTSProvider().stream(text, context):
            if event.execution is not None:
                status = ExecutionStatus.FAILED if self.fail else ExecutionStatus.SUCCEEDED
                event = replace(
                    event,
                    execution=replace(
                        event.execution,
                        provider=self.provider,
                        model=self.model,
                        status=status,
                        error_code="payment_required" if self.fail else None,
                    ),
                )
            if self.fail:
                assert event.execution is not None or event.type == "chunk"
                if event.execution is not None:
                    raise ProviderError("quota exceeded", execution=event.execution, retryable=True)
                continue
            yield event


def _play_one_turn(client: TestClient, session_id: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    url = f"/ws/sessions/{session_id}/voice"
    with suppress(WebSocketDisconnect), client.websocket_connect(url) as socket:
        seen.append(socket.receive_json())
        socket.send_json({"type": "debug.transcript", "transcript": "Guten Tag!"})
        while seen[-1]["type"] not in {"turn.completed", "voice.error"}:
            seen.append(socket.receive_json())
        socket.send_json({"type": "call.end"})
        while seen[-1]["type"] != "call.ended":
            seen.append(socket.receive_json())
    return seen


def _start(client: TestClient, container: Container, provider: str) -> str:
    email = sign_in(client)
    learner_id = str(client.post("/api/learners", json={}).json()["id"])
    VoiceProviders(container.repository.engine).force(email, provider)
    case_id, version = _case(container)
    body = {"learner_id": learner_id, "case_id": case_id, "case_version": version}
    return str(client.post("/api/sessions", json=body).json()["id"])


def _tts_rows(container: Container, session_id: str) -> list[ExecutionRow]:
    with Session(container.repository.engine) as db:
        return list(
            db.scalars(
                select(ExecutionRow)
                .where(
                    ExecutionRow.session_id == session_id,
                    ExecutionRow.operation == "text_to_speech",
                )
                .order_by(ExecutionRow.created_at)
            )
        )


@pytest.mark.parametrize("voice", ["openai", "elevenlabs"])
def test_the_socket_speaks_with_the_pinned_voice(make_container: Any, voice: str) -> None:
    container = make_container(tts_provider="ab")
    openai = TaggedTTS("openai", "gpt-4o-mini-tts")
    elevenlabs = TaggedTTS("elevenlabs", "eleven_v4_turbo")
    container = _with_voices(container, openai, elevenlabs)
    with TestClient(create_app(container)) as client:
        session_id = _start(client, container, voice)
        events = _play_one_turn(client, session_id)
    assert "voice.fallback" not in {e["type"] for e in events}
    spoken, other = (openai, elevenlabs) if voice == "openai" else (elevenlabs, openai)
    assert spoken.spoken and not other.spoken
    assert {r.provider for r in _tts_rows(container, session_id)} == {voice}


def test_an_elevenlabs_failure_before_any_audio_falls_back_to_openai_explicitly(
    make_container: Any, caplog: pytest.LogCaptureFixture
) -> None:
    container = make_container(tts_provider="ab")
    openai = TaggedTTS("openai", "gpt-4o-mini-tts")
    elevenlabs = TaggedTTS("elevenlabs", "eleven_v4_turbo", fail=True)
    container = _with_voices(container, openai, elevenlabs)
    with caplog.at_level(logging.WARNING), TestClient(create_app(container)) as client:
        session_id = _start(client, container, "elevenlabs")
        events = _play_one_turn(client, session_id)
        stored = container.repository.get_session(session_id)
    kinds = [e["type"] for e in events]
    assert "voice.fallback" in kinds and "patient.audio_sent" in kinds
    assert "turn.tts_failed" not in kinds
    assert openai.spoken, "the answer was spoken with the OpenAI voice"
    assert "falling back to OpenAI" in caplog.text
    # The pin stays as assigned; the executions tell which voice really spoke.
    assert _voice_of(stored) == "elevenlabs"
    rows = _tts_rows(container, session_id)
    assert [(r.provider, r.status) for r in rows] == [
        ("elevenlabs", "failed"),
        ("openai", "succeeded"),
    ]


def test_an_openai_failure_is_never_masked_by_a_fallback(make_container: Any) -> None:
    container = make_container(tts_provider="ab")
    container = _with_voices(
        container,
        TaggedTTS("openai", "gpt-4o-mini-tts", fail=True),
        TaggedTTS("elevenlabs", "eleven_v4_turbo"),
    )
    with TestClient(create_app(container)) as client:
        session_id = _start(client, container, "openai")
        kinds = [e["type"] for e in _play_one_turn(client, session_id)]
    assert "turn.tts_failed" in kinds and "voice.fallback" not in kinds


# ----- the Costs page compares the two voices -------------------------------------------------


def test_costs_by_voice_compares_providers_per_session(
    make_container: Any, content_database_url: str, database_url: str
) -> None:
    container = make_container()
    repository = container.repository
    learner = _new_learner(container)
    sessions = [
        repository.create_session(
            ConversationSession(
                id=new_id(),
                learner_id=learner.id,
                case_id="case",
                case_version="1",
                case_hash="hash",
                goal=LearningGoal(),
            )
        )
        for _ in range(3)
    ]
    for session, provider, model, usage in (
        (sessions[0], "openai", "gpt-4o-mini-tts", {"audio_seconds": 60.0}),
        (sessions[1], "elevenlabs", "eleven_v4_turbo", {"characters": 1000}),
        (sessions[2], "elevenlabs", "eleven_v4_turbo", {"characters": 3000}),
        (sessions[2], "elevenlabs", "eleven_v4_turbo", {"characters": 1000}),
    ):
        execution = replace(
            _execution(session.id, provider, model, usage), operation="text_to_speech"
        )
        repository.record_execution(execution)
    report = CostReport(
        create_platform_engine(database_url), create_platform_engine(content_database_url)
    )
    voices = {
        v["provider"]: v
        for v in report.summary(datetime.now(UTC) - timedelta(days=1))["learners"]["by_voice"]
    }
    assert voices["openai"]["sessions"] == 1 and voices["openai"]["calls"] == 1
    assert voices["elevenlabs"]["sessions"] == 2 and voices["elevenlabs"]["calls"] == 3
    assert voices["elevenlabs"]["model"] == "eleven_v4_turbo"
    assert (
        voices["elevenlabs"]["known_micro_eur_per_session"]
        == voices["elevenlabs"]["known_micro_eur"] // 2
    )
