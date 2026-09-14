from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ari.api.app import create_app
from ari.application.contracts import ExecutionContext, LLMRequest
from ari.application.voice_stacks import VoiceStack
from ari.container import Container
from ari.domain.errors import InvalidStateError
from ari.domain.models import new_id
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


def test_voice_stack_is_immutable_and_pins_its_exact_snapshot() -> None:
    original_parameters = {"sample_rate": 24_000, "nested": {"values": [1, 2]}}
    stack = VoiceStack(
        id="pipeline_economy",
        version="1",
        provider="fake",
        models={"llm": "fake"},
        parameters=original_parameters,
    )
    with pytest.raises(TypeError):
        cast(dict[str, str], stack.models)["llm"] = "changed"
    with pytest.raises(TypeError):
        cast(dict[str, Any], stack.parameters)["sample_rate"] = 16_000
    original_parameters["nested"] = {"values": [99]}
    first_snapshot = stack.snapshot()
    snapshot_parameters = cast(dict[str, Any], first_snapshot["parameters"])
    cast(dict[str, list[int]], snapshot_parameters["nested"])["values"].append(3)
    assert stack.snapshot()["parameters"] == {"sample_rate": 24_000, "nested": {"values": [1, 2]}}
    assert stack.resolve_persisted("pipeline_economy", "1", stack.snapshot()) is stack
    stale_snapshot = stack.snapshot()
    cast(dict[str, Any], stale_snapshot["models"])["llm"] = "changed"
    with pytest.raises(InvalidStateError, match="not available exactly"):
        stack.resolve_persisted("pipeline_economy", "1", stale_snapshot)
    with pytest.raises(InvalidStateError, match="not available exactly"):
        stack.resolve_persisted("other", "1", stack.snapshot())


def test_api_persists_the_voice_stack_and_rejects_stack_selection(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        case = client.get("/api/cases").json()[0]
        learner = client.post("/api/learners", json={}).json()
        body = {"learner_id": learner["id"], "case_id": case["id"], "case_version": case["version"]}
        session = client.post("/api/sessions", json=body).json()
        selected = client.post("/api/sessions", json={**body, "voice_stack_id": "other"})

    assert session["voice_stack_id"] == "pipeline_economy"
    assert session["voice_stack_config"]["models"]["llm"] == container.voice_stack.models["llm"]
    assert selected.status_code == 422
    direct = container.orchestrator.create_session(learner["id"], case["id"], case["version"])
    assert direct.voice_stack_config == container.voice_stack.snapshot()


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


@pytest.mark.asyncio
async def test_openai_llm_selects_model_per_operation() -> None:
    selected_models: list[str] = []
    provider = OpenAILLMProvider(
        "offline-key",
        patient_model="patient-luna",
        evaluation_model="evaluation-terra",
        patient_timeout_seconds=1,
        evaluation_timeout_seconds=1,
    )
    provider._client = cast(Any, _OfflineClient(selected_models))
    for operation in ("patient_simulation", "session_evaluation"):
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
    assert selected_models == ["patient-luna", "evaluation-terra"]
