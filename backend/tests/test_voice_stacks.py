from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ari.api.app import create_app
from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.voice_stacks import VoiceStack, VoiceStackRegistry, VoiceTransport
from ari.container import Container
from ari.domain.errors import InvalidStateError
from ari.domain.models import InteractionMode, VoiceProfile, new_id
from ari.infrastructure.providers.openai.llm import OpenAILLMProvider


class _StructuredResult(BaseModel):
    value: str


class _Usage:
    def model_dump(self) -> dict[str, object]:
        return {}


class _Message:
    parsed = _StructuredResult(value="ok")


class _Choice:
    message = _Message()


class _Response:
    id = "offline-response"
    usage = _Usage()
    choices: ClassVar[list[_Choice]] = [_Choice()]


class _Completions:
    def __init__(self, selected_models: list[str]) -> None:
        self._selected_models = selected_models

    async def parse(self, *, model: str, **kwargs: object) -> _Response:
        del kwargs
        self._selected_models.append(model)
        return _Response()


class _Chat:
    def __init__(self, selected_models: list[str]) -> None:
        self.completions = _Completions(selected_models)


class _OfflineClient:
    def __init__(self, selected_models: list[str]) -> None:
        self.chat = _Chat(selected_models)


def _stack(stack_id: str) -> VoiceStack:
    return VoiceStack(
        id=stack_id,
        version="1",
        transport=VoiceTransport.PIPELINE,
        provider="fake",
        models={"llm": "fake"},
        parameters={"sample_rate": 24_000},
    )


def test_voice_stack_registry_is_unique_immutable_and_deterministic() -> None:
    original_parameters = {"sample_rate": 24_000, "nested": {"values": [1, 2]}}
    economy = VoiceStack(
        id="pipeline_economy",
        version="1",
        transport=VoiceTransport.PIPELINE,
        provider="fake",
        models={"llm": "fake"},
        parameters=original_parameters,
    )
    registry = VoiceStackRegistry((economy, _stack("pipeline_low_latency")))

    assert (
        registry.default_for(
            InteractionMode.GUIDED,
            VoiceProfile.ECONOMY,
            preferred_transport=VoiceTransport.PIPELINE,
        )
        is economy
    )
    with pytest.raises(TypeError):
        cast(dict[str, str], economy.models)["llm"] = "changed"
    with pytest.raises(TypeError):
        cast(dict[str, Any], economy.parameters)["sample_rate"] = 16_000
    original_parameters["nested"] = {"values": [99]}
    first_snapshot = economy.snapshot()
    snapshot_parameters = cast(dict[str, Any], first_snapshot["parameters"])
    cast(dict[str, list[int]], snapshot_parameters["nested"])["values"].append(3)
    assert economy.snapshot()["parameters"] == {
        "sample_rate": 24_000,
        "nested": {"values": [1, 2]},
    }
    assert registry.resolve_persisted("pipeline_economy", "1", economy.snapshot()) is economy
    stale_snapshot = economy.snapshot()
    cast(dict[str, Any], stale_snapshot["models"])["llm"] = "changed"
    with pytest.raises(InvalidStateError, match="not available exactly"):
        registry.resolve_persisted("pipeline_economy", "1", stale_snapshot)
    with pytest.raises(ValueError, match="unique"):
        VoiceStackRegistry((economy, _stack("pipeline_economy")))


def test_api_selects_and_persists_explicit_stack_and_rejects_unknown(
    container: Container,
) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        case = client.get("/api/cases").json()[0]
        learner = client.post("/api/learners", json={}).json()
        default_session = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
            },
        ).json()
        selected = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
                "voice_stack_id": "realtime_quality",
            },
        )
        unknown = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
                "voice_stack_id": "unknown-stack",
            },
        )

    assert default_session["voice_stack_id"] == "pipeline_economy"
    assert selected.status_code == 201
    assert selected.json()["voice_stack_id"] == "realtime_quality"
    assert selected.json()["voice_profile"] == "quality"
    assert selected.json()["voice_stack_config"]["models"]["realtime"] == "gpt-realtime-2.1"
    assert unknown.status_code == 404

    direct = container.orchestrator.create_session(learner["id"], case["id"], case["version"])
    assert (
        container.voice_stacks.resolve_persisted(
            direct.voice_stack_id,
            direct.voice_stack_version,
            direct.voice_stack_config,
        ).id
        == "pipeline_economy"
    )


def test_fake_mode_rejects_unavailable_explicit_realtime_stack(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        case = client.get("/api/cases").json()[0]
        learner = client.post("/api/learners", json={}).json()
        session = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
                "voice_stack_id": "realtime_economy",
            },
        ).json()
        with client.websocket_connect(f"/ws/sessions/{session['id']}/voice") as socket:
            error = socket.receive_json()

    assert error["type"] == "voice.error"
    assert "unavailable" in error["data"]["message"]


def test_voice_socket_rejects_stale_persisted_stack_cleanly(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        case = client.get("/api/cases").json()[0]
        learner = client.post("/api/learners", json={}).json()
        session = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
            },
        ).json()
        stored = container.repository.get_session(session["id"])
        stale_snapshot = dict(stored.voice_stack_config)
        stale_snapshot["provider"] = "legacy_unknown"
        stale = replace(
            stored,
            id=new_id(),
            voice_stack_config=stale_snapshot,
        )
        container.repository.create_session(stale)

        with client.websocket_connect(f"/ws/sessions/{stale.id}/voice") as socket:
            error = socket.receive_json()

    assert error["type"] == "voice.error"
    assert error["data"]["message"] == "The persisted voice stack is unavailable"
    assert "not available exactly" in error["data"]["detail"]


def test_legacy_voice_transport_setting_only_selects_the_default_stack(
    container: Container,
) -> None:
    pipeline_settings = container.settings.model_copy(update={"voice_transport": "pipeline"})
    services = replace(container, settings=pipeline_settings)
    app = create_app(services)
    with TestClient(app) as client:
        case = client.get("/api/cases").json()[0]
        learner = client.post("/api/learners", json={}).json()
        default_session = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
            },
        ).json()
        explicit_session = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
                "voice_stack_id": "realtime_quality",
            },
        ).json()

    assert default_session["voice_stack_id"] == "pipeline_economy"
    assert explicit_session["voice_stack_id"] == "realtime_quality"


def test_production_cannot_enable_automatic_schema_creation(container: Container) -> None:
    production_settings = container.settings.model_copy(
        update={"environment": "production", "auto_create_schema": True}
    )
    app = create_app(replace(container, settings=production_settings))

    with pytest.raises(RuntimeError, match="cannot be enabled in production"), TestClient(app):
        pass


@pytest.mark.asyncio
async def test_openai_llm_constructor_compatibility_and_dedicated_grounding_model() -> None:
    selected_models: list[str] = []
    provider = OpenAILLMProvider(
        "offline-key",
        patient_model="patient-luna",
        evaluation_model="evaluation-terra",
        patient_timeout_seconds=1,
        evaluation_timeout_seconds=1,
        grounding_model="grounding-luna",
    )
    provider._client = cast(Any, _OfflineClient(selected_models))
    for operation in ("patient_simulation", "grounding_audit", "session_evaluation"):
        await provider.generate_structured(
            LLMRequest(
                messages=({"role": "user", "content": "offline"},),
                context=ExecutionContext(
                    session_id="session",
                    operation=operation,
                    case_version="1",
                    case_hash="hash",
                ),
            ),
            _StructuredResult,
        )
    assert selected_models == ["patient-luna", "grounding-luna", "evaluation-terra"]

    fallback_models: list[str] = []
    compatible = OpenAILLMProvider(
        "offline-key",
        patient_model="patient-luna",
        evaluation_model="evaluation-terra",
        patient_timeout_seconds=1,
        evaluation_timeout_seconds=1,
    )
    compatible._client = cast(Any, _OfflineClient(fallback_models))
    await compatible.generate_structured(
        LLMRequest(
            messages=({"role": "user", "content": "offline"},),
            context=ExecutionContext(
                session_id="session",
                operation="grounding_audit",
                case_version="1",
                case_hash="hash",
            ),
        ),
        _StructuredResult,
    )
    assert fallback_models == ["patient-luna"]
