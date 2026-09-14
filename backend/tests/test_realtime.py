from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, ClassVar

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from ari.api.app import create_app
from ari.application.contracts import ExecutionContext
from ari.application.ports.realtime_voice import (
    RealtimeProviderEvent,
    RealtimeResponseKind,
    RealtimeSimulationSpec,
)
from ari.application.services.realtime import RealtimeTurnAssembler
from ari.container import Container
from ari.domain.errors import ProviderError
from ari.domain.models import (
    ExecutionRecord,
    ExecutionStatus,
    InteractionMode,
    VoiceProfile,
    new_id,
)
from ari.infrastructure.providers.openai import realtime_voice as realtime_module
from ari.infrastructure.providers.openai.realtime_voice import (
    OpenAIRealtimeCall,
    OpenAIRealtimeVoiceEngine,
)


def _execution(session_id: str, operation: str, request_id: str) -> ExecutionRecord:
    return ExecutionRecord(
        id=new_id(),
        session_id=session_id,
        operation=operation,
        provider="fake-realtime",
        model="fake-realtime-v1",
        status=ExecutionStatus.SUCCEEDED,
        prompt_version="realtime-patient-v1",
        prompt_hash="hash",
        case_version="1.0",
        case_hash="case-hash",
        latency_ms=4,
        usage={"input_token_details": {"audio_tokens": 10}},
        estimated_cost_usd=0.001,
        provider_request_id=request_id,
    )


class FakeRealtimeCall:
    call_id = "rtc_fake"
    provider = "fake-realtime"
    model = "fake-realtime-v1"
    answer_sdp = "v=0\r\na=fake-answer\r\n"

    def __init__(self, context: ExecutionContext) -> None:
        self.execution = _execution(context.session_id, "realtime_voice.connect", self.call_id)
        self.last_exact_text: str | None = None

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        yield RealtimeProviderEvent(
            "patient_speech_started",
            response_id="opening-1",
            response_kind=RealtimeResponseKind.OPENING,
        )
        yield RealtimeProviderEvent(
            "patient_transcript_final",
            text=(
                "Guten Morgen. Ich heiße Sabine Keller. "
                "Die Schmerzen sind hier rechts oben unter den Rippen."
            ),
            response_id="opening-1",
            response_kind=RealtimeResponseKind.OPENING,
        )
        opening_execution = replace(
            _execution(self.execution.session_id, "realtime_voice.opening", "opening-1"),
            operation="realtime_voice.opening",
        )
        yield RealtimeProviderEvent(
            "response_completed",
            response_id="opening-1",
            response_status="completed",
            execution=opening_execution,
            response_kind=RealtimeResponseKind.OPENING,
        )
        yield RealtimeProviderEvent("user_speech_started", input_item_id="input-1")
        yield RealtimeProviderEvent("user_speech_stopped", input_item_id="input-1")
        yield RealtimeProviderEvent(
            "user_transcript_final",
            text="Seit wann bestehen die Schmerzen?",
            input_item_id="input-1",
        )
        yield RealtimeProviderEvent("patient_speech_started", response_id="response-1")
        yield RealtimeProviderEvent(
            "patient_transcript_delta", text="Seit gestern ", response_id="response-1"
        )
        yield RealtimeProviderEvent(
            "patient_transcript_final",
            text=self.last_exact_text or "Seit gestern Abend.",
            response_id="response-1",
        )
        response_execution = _execution(
            self.execution.session_id, "realtime_voice.response", "response-1"
        )
        yield RealtimeProviderEvent(
            "response_completed",
            response_id="response-1",
            usage={"input_token_details": {"audio_tokens": 10}},
            execution=response_execution,
        )
        yield RealtimeProviderEvent("response_completed", response_id="response-1")

    async def close(self) -> None:
        return None

    async def update_simulation(self, simulation: RealtimeSimulationSpec) -> None:
        del simulation

    async def begin_user_turn(self) -> None:
        return None

    async def commit_user_turn(self) -> None:
        return None

    async def interrupt_response(self) -> None:
        return None

    async def create_response(
        self,
        *,
        kind: RealtimeResponseKind = RealtimeResponseKind.PATIENT_ANSWER,
        exact_text: str | None = None,
    ) -> None:
        if kind is RealtimeResponseKind.PATIENT_ANSWER:
            assert exact_text
            self.last_exact_text = exact_text
        del kind
        return None


class FakeRealtimeEngine:
    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> FakeRealtimeCall:
        assert offer_sdp.startswith("v=0")
        assert "sole source of truth" in simulation.instructions
        assert profile is VoiceProfile.ECONOMY
        assert interaction_mode is InteractionMode.GUIDED
        return FakeRealtimeCall(context)


class DrainingRealtimeCall(FakeRealtimeCall):
    def __init__(self, context: ExecutionContext) -> None:
        super().__init__(context)
        self._closed = asyncio.Event()

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        yield RealtimeProviderEvent(
            "user_transcript_final", text="Hello", input_item_id="input-final"
        )
        yield RealtimeProviderEvent(
            "patient_transcript_final", text="Hello, doctor.", response_id="response-final"
        )
        await self._closed.wait()
        yield RealtimeProviderEvent(
            "response_completed",
            response_id="response-final",
            execution=_execution(
                self.execution.session_id, "realtime_voice.response", "response-final"
            ),
        )

    async def close(self) -> None:
        self._closed.set()


class DrainingRealtimeEngine(FakeRealtimeEngine):
    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> DrainingRealtimeCall:
        del offer_sdp, simulation, profile, interaction_mode
        return DrainingRealtimeCall(context)


class CancelledThenCompletedCall(FakeRealtimeCall):
    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        yield RealtimeProviderEvent("user_speech_started", input_item_id="input-1")
        yield RealtimeProviderEvent(
            "user_transcript_final", text="Haben Sie erbrochen?", input_item_id="input-1"
        )
        yield RealtimeProviderEvent(
            "response_completed", response_id="response-1", response_status="cancelled"
        )
        yield RealtimeProviderEvent("user_speech_started", input_item_id="input-2")
        yield RealtimeProviderEvent(
            "user_transcript_final", text="Haben Sie Fieber?", input_item_id="input-2"
        )
        await asyncio.sleep(0)
        yield RealtimeProviderEvent(
            "patient_transcript_final",
            text=self.last_exact_text or "Heute Morgen waren es 38,2 Grad.",
            response_id="response-2",
        )
        yield RealtimeProviderEvent(
            "response_completed",
            response_id="response-2",
            response_status="completed",
            execution=_execution(
                self.execution.session_id, "realtime_voice.response", "response-2"
            ),
        )


class CancelledThenCompletedEngine(FakeRealtimeEngine):
    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> CancelledThenCompletedCall:
        del offer_sdp, simulation, profile, interaction_mode
        return CancelledThenCompletedCall(context)


class MissingResponseCall(FakeRealtimeCall):
    def __init__(self, context: ExecutionContext) -> None:
        super().__init__(context)
        self._closed = asyncio.Event()

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        yield RealtimeProviderEvent(
            "user_transcript_final", text="Haben Sie erbrochen?", input_item_id="input-final"
        )
        await self._closed.wait()

    async def close(self) -> None:
        self._closed.set()


class MissingResponseEngine(FakeRealtimeEngine):
    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> MissingResponseCall:
        del offer_sdp, simulation, profile, interaction_mode
        return MissingResponseCall(context)


class SpeechInProgressCall(FakeRealtimeCall):
    def __init__(self, context: ExecutionContext) -> None:
        super().__init__(context)
        self._closed = asyncio.Event()

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        yield RealtimeProviderEvent("user_speech_started", input_item_id="input-speaking")
        await self._closed.wait()
        yield RealtimeProviderEvent(
            "user_transcript_final",
            text="Haben Sie Atemnot?",
            input_item_id="input-speaking",
        )

    async def close(self) -> None:
        self._closed.set()


class SpeechInProgressEngine(FakeRealtimeEngine):
    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> SpeechInProgressCall:
        del offer_sdp, simulation, profile, interaction_mode
        return SpeechInProgressCall(context)


class ControlledGuidedCall(FakeRealtimeCall):
    def __init__(self, context: ExecutionContext) -> None:
        super().__init__(context)
        self.queue: asyncio.Queue[RealtimeProviderEvent | None] = asyncio.Queue()
        self.begin_count = 0
        self.commit_count = 0
        self.interrupt_count = 0
        self.answer_count = 0
        self.opening_count = 0

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        while (event := await self.queue.get()) is not None:
            yield event

    async def begin_user_turn(self) -> None:
        self.begin_count += 1

    async def commit_user_turn(self) -> None:
        self.commit_count += 1
        await self.queue.put(
            RealtimeProviderEvent(
                "user_transcript_final",
                text="Haben Sie erbrochen?",
                input_item_id="guided-input-1",
            )
        )

    async def interrupt_response(self) -> None:
        self.interrupt_count += 1

    async def create_response(
        self,
        *,
        kind: RealtimeResponseKind = RealtimeResponseKind.PATIENT_ANSWER,
        exact_text: str | None = None,
    ) -> None:
        if kind is RealtimeResponseKind.OPENING:
            self.opening_count += 1
            assert exact_text is not None
            await self.queue.put(
                RealtimeProviderEvent(
                    "patient_speech_started",
                    response_id="guided-opening",
                    response_kind=kind,
                )
            )
            await self.queue.put(
                RealtimeProviderEvent(
                    "patient_transcript_final",
                    text=exact_text,
                    response_id="guided-opening",
                    response_kind=kind,
                )
            )
            await self.queue.put(
                RealtimeProviderEvent(
                    "response_completed",
                    response_id="guided-opening",
                    response_status="completed",
                    execution=replace(
                        _execution(
                            self.execution.session_id,
                            "realtime_voice.opening",
                            "guided-opening",
                        ),
                        operation="realtime_voice.opening",
                    ),
                    response_kind=kind,
                )
            )
            await self.queue.put(
                RealtimeProviderEvent(
                    "response_completed",
                    response_id="guided-opening",
                    response_status="completed",
                    execution=replace(
                        _execution(
                            self.execution.session_id,
                            "realtime_voice.opening",
                            "guided-opening-duplicate",
                        ),
                        operation="realtime_voice.opening",
                    ),
                    response_kind=kind,
                )
            )
            return
        self.answer_count += 1
        response_id = f"guided-response-{self.answer_count}"
        assert exact_text
        self.last_exact_text = exact_text
        await self.queue.put(
            RealtimeProviderEvent("patient_speech_started", response_id=response_id)
        )
        await self.queue.put(
            RealtimeProviderEvent(
                "patient_transcript_final",
                text=self.last_exact_text,
                response_id=response_id,
            )
        )
        await self.queue.put(
            RealtimeProviderEvent(
                "response_completed",
                response_id=response_id,
                response_status="completed",
                execution=_execution(
                    self.execution.session_id,
                    "realtime_voice.response",
                    response_id,
                ),
            )
        )

    async def close(self) -> None:
        await self.queue.put(None)


class ControlledGuidedEngine(FakeRealtimeEngine):
    def __init__(self) -> None:
        self.call: ControlledGuidedCall | None = None

    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> ControlledGuidedCall:
        del offer_sdp, simulation, profile
        assert interaction_mode is InteractionMode.GUIDED
        self.call = ControlledGuidedCall(context)
        return self.call


class DeviatingOpeningCall(ControlledGuidedCall):
    async def create_response(
        self,
        *,
        kind: RealtimeResponseKind = RealtimeResponseKind.PATIENT_ANSWER,
        exact_text: str | None = None,
    ) -> None:
        if kind is not RealtimeResponseKind.OPENING:
            await super().create_response(kind=kind, exact_text=exact_text)
            return
        assert exact_text is not None
        await self.queue.put(
            RealtimeProviderEvent(
                "patient_transcript_final",
                text=f"{exact_text} Ein zusätzlicher Satz.",
                response_id="deviating-opening",
                response_kind=kind,
            )
        )
        await self.queue.put(
            RealtimeProviderEvent(
                "response_completed",
                response_id="deviating-opening",
                response_status="completed",
                execution=replace(
                    _execution(
                        self.execution.session_id,
                        "realtime_voice.opening",
                        "deviating-opening",
                    ),
                    operation="realtime_voice.opening",
                ),
                response_kind=kind,
            )
        )


class DeviatingOpeningEngine(ControlledGuidedEngine):
    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> DeviatingOpeningCall:
        del offer_sdp, simulation, profile, interaction_mode
        call = DeviatingOpeningCall(context)
        self.call = call
        return call


class LimitFailureCall(FakeRealtimeCall):
    async def interrupt_response(self) -> None:
        raise RuntimeError("forced interrupt failure")

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        opening = (
            "Guten Morgen. Ich heiße Sabine Keller. "
            "Die Schmerzen sind hier rechts oben unter den Rippen."
        )
        yield RealtimeProviderEvent(
            "patient_transcript_final",
            text=opening,
            response_id="limit-opening",
            response_kind=RealtimeResponseKind.OPENING,
        )
        yield RealtimeProviderEvent(
            "response_completed",
            response_id="limit-opening",
            response_status="completed",
            execution=replace(
                _execution(
                    self.execution.session_id,
                    "realtime_voice.opening",
                    "limit-opening",
                ),
                operation="realtime_voice.opening",
            ),
            response_kind=RealtimeResponseKind.OPENING,
        )
        yield RealtimeProviderEvent(
            "user_transcript_final",
            text="Erzählen Sie mir bitte alles.",
            input_item_id="limit-input",
        )
        yield RealtimeProviderEvent("patient_speech_started", response_id="limit-response")
        long_text = " ".join(f"wort{index}" for index in range(46))
        yield RealtimeProviderEvent(
            "patient_transcript_delta",
            text=long_text,
            response_id="limit-response",
        )
        yield RealtimeProviderEvent(
            "patient_transcript_final",
            text=long_text,
            response_id="limit-response",
        )
        yield RealtimeProviderEvent(
            "response_completed",
            response_id="limit-response",
            response_status="completed",
            execution=_execution(
                self.execution.session_id,
                "realtime_voice.response",
                "limit-response",
            ),
        )


class LimitFailureEngine(FakeRealtimeEngine):
    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> LimitFailureCall:
        del offer_sdp, simulation, profile, interaction_mode
        return LimitFailureCall(context)


class ResponseCreateFailureCall(ControlledGuidedCall):
    def __init__(self, context: ExecutionContext, *, provider_error: bool) -> None:
        super().__init__(context)
        self.provider_error = provider_error

    async def create_response(
        self,
        *,
        kind: RealtimeResponseKind = RealtimeResponseKind.PATIENT_ANSWER,
        exact_text: str | None = None,
    ) -> None:
        if kind is RealtimeResponseKind.OPENING:
            await super().create_response(kind=kind, exact_text=exact_text)
            return
        if self.provider_error:
            failed = replace(
                _execution(
                    self.execution.session_id,
                    "realtime_voice.response_create",
                    "failed-create",
                ),
                status=ExecutionStatus.FAILED,
                error_code="forced_failure",
                error_message="forced response creation failure",
            )
            raise ProviderError("forced response creation failure", execution=failed)
        raise RuntimeError("forced generic response creation failure")


class ResponseCreateFailureEngine(FakeRealtimeEngine):
    def __init__(self, *, provider_error: bool) -> None:
        self.provider_error = provider_error
        self.call: ResponseCreateFailureCall | None = None

    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> ResponseCreateFailureCall:
        del offer_sdp, simulation, profile, interaction_mode
        self.call = ResponseCreateFailureCall(
            context,
            provider_error=self.provider_error,
        )
        return self.call


class NonTerminalBargeInCall(ControlledGuidedCall):
    async def commit_user_turn(self) -> None:
        self.commit_count += 1
        await self.queue.put(
            RealtimeProviderEvent(
                "user_transcript_final",
                text=f"Frage {self.commit_count}",
                input_item_id=f"nonterminal-input-{self.commit_count}",
            )
        )

    async def create_response(
        self,
        *,
        kind: RealtimeResponseKind = RealtimeResponseKind.PATIENT_ANSWER,
        exact_text: str | None = None,
    ) -> None:
        if kind is RealtimeResponseKind.OPENING:
            await super().create_response(kind=kind, exact_text=exact_text)
            return
        self.answer_count += 1
        await self.queue.put(
            RealtimeProviderEvent(
                "patient_speech_started",
                response_id=f"nonterminal-response-{self.answer_count}",
            )
        )


class NonTerminalBargeInEngine(FakeRealtimeEngine):
    def __init__(self) -> None:
        self.call: NonTerminalBargeInCall | None = None

    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> NonTerminalBargeInCall:
        del offer_sdp, simulation, profile, interaction_mode
        self.call = NonTerminalBargeInCall(context)
        return self.call


def _create_session(client: TestClient) -> dict[str, object]:
    case = client.get("/api/cases").json()[0]
    learner = client.post("/api/learners", json={"target_cefr": "C1"}).json()
    response = client.post(
        "/api/sessions",
        json={
            "learner_id": learner["id"],
            "case_id": case["id"],
            "case_version": case["version"],
            "voice_profile": "economy",
            "voice_stack_id": "realtime_economy",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_realtime_sdp_sideband_persists_one_idempotent_turn(container: Container) -> None:
    services = replace(container, realtime_voice=FakeRealtimeEngine())
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        assert session["voice_profile"] == "economy"
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            sdp = client.post(
                f"/api/sessions/{session_id}/voice/realtime",
                content="v=0\r\na=fake-offer\r\n",
                headers={"content-type": "application/sdp"},
            )
            assert sdp.status_code == 200
            assert sdp.headers["content-type"].startswith("application/sdp")
            event_types: list[str] = []
            while "turn.completed" not in event_types:
                event_types.append(socket.receive_json()["type"])
            socket.send_json({"type": "call.end"})
            while socket.receive_json()["type"] != "call.ended":
                pass

        persisted = client.get(f"/api/sessions/{session_id}").json()
        assert len(persisted["turns"]) == 1
        assert persisted["patient_opening"]["status"] == "completed"
        assert persisted["patient_opening"]["provider_response_id"] == "opening-1"
        assert persisted["turns"][0]["provider_response_id"] == "response-1"
        assert persisted["turns"][0]["delivery_status"] == "unconfirmed"
        assert persisted["turns"][0]["audio_delivered_at"] is None
        assert jsonable_encoder(
            services.repository.get_session(session_id).turns[0].canonical_response
        )["text"]
        assert (
            jsonable_encoder(
                services.repository.get_session(session_id).turns[0].observed_response_text
            )
            == jsonable_encoder(
                services.repository.get_session(session_id).turns[0].canonical_response
            )["text"]
        )
        operations = [
            item["operation"]
            for item in jsonable_encoder(services.repository.get_session(session_id).executions)
        ]
        assert operations.count("realtime_voice.response") == 1
        assert operations.count("realtime_voice.opening") == 1
        assert {"realtime.connected", "patient.speech_started"} <= set(event_types)
        metric = services.repository.get_voice_turn_metric(persisted["turns"][0]["id"])
        assert metric is not None
        assert metric.llm_total_ms is None
        assert metric.speech_end_to_first_audio_sent_ms is None


def test_realtime_client_playback_signal_marks_audio_started(container: Container) -> None:
    services = replace(container, realtime_voice=FakeRealtimeEngine())
    with TestClient(create_app(services)) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            socket.receive_json()
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            turn = None
            while turn is None:
                event = socket.receive_json()
                if event["type"] == "turn.completed":
                    turn = event["data"]["turn"]
            socket.send_json(
                {
                    "type": "audio.playback_started",
                    "turn_id": turn["id"],
                    "response_id": turn["provider_response_id"],
                    "audio_stream_id": turn["audio_stream_id"],
                    "last_index": 0,
                }
            )
            socket.send_json({"type": "call.end"})
            while socket.receive_json()["type"] != "call.ended":
                pass
        stored = client.get(f"/api/sessions/{session_id}").json()
        assert stored["turns"][0]["delivery_status"] == "unconfirmed"
        assert (
            jsonable_encoder(services.repository.get_session(session_id).turns[0].revealed_fact_ids)
            == []
        )


def test_guided_controls_are_idempotent_and_opening_is_separate(
    container: Container,
) -> None:
    engine = ControlledGuidedEngine()
    services = replace(container, realtime_voice=engine)
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            event_types: list[str] = []
            while "user.turn.ready" not in event_types:
                event_types.append(socket.receive_json()["type"])
            socket.send_json({"type": "user.turn.start"})
            socket.send_json({"type": "user.turn.start"})
            while socket.receive_json()["type"] != "user.turn.recording":
                pass
            socket.send_json({"type": "user.turn.finish"})
            socket.send_json({"type": "user.turn.finish"})
            socket.send_json({"type": "user.turn.start"})
            while socket.receive_json()["type"] != "turn.completed":
                pass
            socket.send_json({"type": "call.end"})
            while socket.receive_json()["type"] != "call.ended":
                pass

        assert engine.call is not None
        assert engine.call.opening_count == 1
        assert engine.call.begin_count == 1
        assert engine.call.commit_count == 1
        assert engine.call.answer_count == 1
        persisted = client.get(f"/api/sessions/{session_id}").json()
        assert persisted["patient_opening"]["status"] == "completed"
        assert persisted["patient_opening"]["spoken_text"] == persisted["patient_opening"]["text"]
        assert len(persisted["turns"]) == 1
        assert persisted["turns"][0]["user_text"] == "Haben Sie erbrochen?"
        operations = [
            item["operation"]
            for item in jsonable_encoder(services.repository.get_session(session_id).executions)
        ]
        assert operations.count("realtime_voice.opening") == 1


def test_existing_session_with_turns_does_not_receive_late_opening(
    container: Container,
) -> None:
    engine = ControlledGuidedEngine()
    services = replace(container, realtime_voice=engine)
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        services.orchestrator.activate(session_id)
        services.orchestrator.reserve_transcript(session_id, "Ein vorhandener Satz.")
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            while socket.receive_json()["type"] != "user.turn.ready":
                pass
            socket.send_json({"type": "call.end"})
            while socket.receive_json()["type"] != "call.ended":
                pass

        assert engine.call is not None
        assert engine.call.opening_count == 0
        assert client.get(f"/api/sessions/{session_id}").json()["patient_opening"] is None


def test_opening_deviation_is_persisted_and_flagged(container: Container) -> None:
    engine = DeviatingOpeningEngine()
    services = replace(container, realtime_voice=engine)
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            event_types: list[str] = []
            while "user.turn.ready" not in event_types:
                event_types.append(socket.receive_json()["type"])
            socket.send_json({"type": "call.end"})
            while socket.receive_json()["type"] != "call.ended":
                pass

        opening = client.get(f"/api/sessions/{session_id}").json()["patient_opening"]
        assert opening["status"] == "failed"
        assert opening["spoken_text"].endswith("Ein zusätzlicher Satz.")
        assert "patient.opening_failed" in event_types


def test_word_limit_interrupt_failure_is_nonterminal(container: Container) -> None:
    services = replace(container, realtime_voice=LimitFailureEngine())
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            event_types: list[str] = []
            while "turn.completed" not in event_types:
                event_types.append(socket.receive_json()["type"])
            socket.send_json({"type": "call.end"})
            while socket.receive_json()["type"] != "call.ended":
                pass

        assert "response.limit_failed" in event_types
        assert "voice.error" not in event_types
        turn = client.get(f"/api/sessions/{session_id}").json()["turns"][0]
        assert turn["provider_response_status"] == "canonical_response_mismatch"
        assert turn["delivery_status"] == "failed"
        assert any(
            item["operation"] == "realtime_voice.interrupt" and item["status"] == "failed"
            for item in jsonable_encoder(services.repository.get_session(session_id).executions)
        )


@pytest.mark.parametrize("provider_error", [True, False])
def test_response_create_failure_returns_guided_mode_to_ready(
    container: Container, provider_error: bool
) -> None:
    engine = ResponseCreateFailureEngine(provider_error=provider_error)
    services = replace(container, realtime_voice=engine)
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            while socket.receive_json()["type"] != "user.turn.ready":
                pass
            socket.send_json({"type": "user.turn.start"})
            while socket.receive_json()["type"] != "user.turn.recording":
                pass
            socket.send_json({"type": "user.turn.finish"})
            failure_events: list[str] = []
            while "response.create_failed" not in failure_events:
                failure_events.append(socket.receive_json()["type"])
            assert "user.turn.ready" in failure_events
            socket.send_json({"type": "user.turn.start"})
            while socket.receive_json()["type"] != "user.turn.recording":
                pass
            socket.send_json({"type": "call.end"})
            while socket.receive_json()["type"] != "call.ended":
                pass

        assert any(
            item["operation"] == "realtime_voice.response_create" and item["status"] == "failed"
            for item in jsonable_encoder(services.repository.get_session(session_id).executions)
        )


def test_http_end_cancels_response_waiting_behind_nonterminal_barge_in(
    container: Container,
) -> None:
    engine = NonTerminalBargeInEngine()
    services = replace(container, realtime_voice=engine)
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            while socket.receive_json()["type"] != "user.turn.ready":
                pass
            socket.send_json({"type": "user.turn.start"})
            while socket.receive_json()["type"] != "user.turn.recording":
                pass
            socket.send_json({"type": "user.turn.finish"})
            while socket.receive_json()["type"] != "patient.speech_started":
                pass
            socket.send_json({"type": "user.turn.start"})
            while socket.receive_json()["type"] != "user.turn.recording":
                pass
            socket.send_json({"type": "user.turn.finish"})
            while socket.receive_json()["type"] != "user.transcript_final":
                pass
            started = time.perf_counter()
            ended = client.post(f"/api/sessions/{session_id}/end", json={})
            elapsed = time.perf_counter() - started

        assert ended.status_code == 200
        assert elapsed < 10
        assert engine.call is not None
        assert engine.call.answer_count == 1
        assert len(ended.json()["turns"]) == 2


def test_call_end_drains_final_response_before_acknowledgement(container: Container) -> None:
    services = replace(container, realtime_voice=DrainingRealtimeEngine())
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            response = client.post(
                f"/api/sessions/{session_id}/voice/realtime",
                content="v=0\r\n",
                headers={"content-type": "application/sdp"},
            )
            assert response.status_code == 200
            while socket.receive_json()["type"] != "realtime.connected":
                pass
            socket.send_json({"type": "call.end"})
            event_types: list[str] = []
            while not event_types or event_types[-1] != "call.ended":
                event_types.append(socket.receive_json()["type"])

        assert "turn.persisted" in event_types
        persisted = client.get(f"/api/sessions/{session_id}").json()
        assert len(persisted["turns"]) == 1


def test_cancelled_empty_response_does_not_block_following_turns(
    container: Container,
) -> None:
    services = replace(container, realtime_voice=CancelledThenCompletedEngine())
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            completed = 0
            while completed < 2:
                if socket.receive_json()["type"] == "turn.completed":
                    completed += 1
            socket.send_json({"type": "call.end"})
            while socket.receive_json()["type"] != "call.ended":
                pass

        persisted = client.get(f"/api/sessions/{session_id}").json()
        assert len(persisted["turns"]) == 2
        assert persisted["turns"][0]["provider_response_status"] == "cancelled"
        assert persisted["turns"][0]["patient_text"] == ""
        assert persisted["turns"][1]["provider_response_status"] == "completed"
        assert jsonable_encoder(
            services.repository.get_session(session_id).turns[1].canonical_response
        )["text"]
        assert (
            jsonable_encoder(
                services.repository.get_session(session_id).turns[1].observed_response_text
            )
            == jsonable_encoder(
                services.repository.get_session(session_id).turns[1].canonical_response
            )["text"]
        )


def test_http_end_finalizes_voice_without_websocket_control_ack(
    container: Container,
) -> None:
    services = replace(container, realtime_voice=MissingResponseEngine())
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            while socket.receive_json()["type"] != "turn.persisted":
                pass
            completed = client.post(f"/api/sessions/{session_id}/end", json={})

        assert completed.status_code == 200
        payload = completed.json()
        assert payload["status"] == "completed"
        assert len(payload["turns"]) == 1
        assert payload["turns"][0]["user_text"] == "Haben Sie erbrochen?"
        assert payload["turns"][0]["provider_response_status"] == "completed"
        assert jsonable_encoder(
            services.repository.get_session(session_id).turns[0].canonical_response
        )["text"]
        assert (
            jsonable_encoder(
                services.repository.get_session(session_id).turns[0].observed_response_text
            )
            is None
        )
        assert payload["ended_at"] is not None
        replay = client.post(f"/api/sessions/{session_id}/end", json={}).json()
        assert replay["evaluation"]["created_at"] == payload["evaluation"]["created_at"]


def test_http_end_during_user_speech_persists_final_provider_transcript(
    container: Container,
) -> None:
    services = replace(container, realtime_voice=SpeechInProgressEngine())
    app = create_app(services)
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = str(session["id"])
        with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as socket:
            assert socket.receive_json()["type"] == "call.started"
            assert (
                client.post(
                    f"/api/sessions/{session_id}/voice/realtime",
                    content="v=0\r\n",
                    headers={"content-type": "application/sdp"},
                ).status_code
                == 200
            )
            while socket.receive_json()["type"] != "user.speech_started":
                pass
            completed = client.post(f"/api/sessions/{session_id}/end", json={})

        assert completed.status_code == 200
        payload = completed.json()
        assert payload["status"] == "completed"
        assert len(payload["turns"]) == 1
        assert payload["turns"][0]["user_text"] == "Haben Sie Atemnot?"
        assert payload["turns"][0]["provider_response_status"] == "awaiting_response"


def test_turn_assembler_handles_delayed_transcript_and_barge_in() -> None:
    assembler = RealtimeTurnAssembler()
    assembler.handle(RealtimeProviderEvent("patient_speech_started", response_id="r1"))
    assembler.handle(
        RealtimeProviderEvent("patient_transcript_delta", text="I have ", response_id="r1")
    )
    assembler.handle(RealtimeProviderEvent("user_speech_started", response_id="r1"))
    assembler.handle(
        RealtimeProviderEvent("patient_interrupted", response_id="r1", audio_end_ms=420)
    )
    assembler.handle(
        RealtimeProviderEvent("patient_transcript_final", text="I have pain.", response_id="r1")
    )
    assert assembler.handle(RealtimeProviderEvent("response_completed", response_id="r1")) is None
    completed = assembler.handle(
        RealtimeProviderEvent("user_transcript_final", text="Hello", input_item_id="i1")
    )
    assert completed is not None
    assert completed.interrupted is True
    assert completed.interruption_audio_end_ms == 420
    assert completed.user_text == "Hello"
    assert completed.patient_text == "I have pain."
    assert assembler.handle(RealtimeProviderEvent("response_completed", response_id="r1")) is None


def test_turn_assembler_drains_missing_transcript_before_later_response() -> None:
    assembler = RealtimeTurnAssembler()
    assembler.handle(
        RealtimeProviderEvent("user_transcript_final", text="first", input_item_id="i1")
    )
    assembler.handle(
        RealtimeProviderEvent("response_completed", response_id="r1", response_status="completed")
    )
    assembler.handle(
        RealtimeProviderEvent("user_transcript_final", text="second", input_item_id="i2")
    )
    assembler.handle(
        RealtimeProviderEvent("patient_transcript_final", text="answer", response_id="r2")
    )
    assert (
        assembler.handle(
            RealtimeProviderEvent(
                "response_completed", response_id="r2", response_status="completed"
            )
        )
        is None
    )
    completed = assembler.drain_ready()
    assert [item.response_status for item in completed] == ["missing_transcript", "completed"]
    assert [item.user_text for item in completed] == ["first", "second"]


def test_realtime_simulation_contains_case_scope_and_no_external_tools(
    container: Container,
) -> None:
    case = container.cases.get("ARI-FSP-001", "1.0")
    spec = container.realtime_simulation.build(case)
    assert "history.hypertension" in spec.instructions
    assert "surgery.caesarean_sections" in spec.instructions
    assert "Do not use or mention the Internet" in spec.instructions
    assert "Medical-history questions" in spec.instructions
    assert "concern.surgery" not in spec.instructions
    timed_spec = container.realtime_simulation.build(case, include_timed_facts=True)
    assert "concern.surgery" in timed_spec.instructions


def test_patient_opening_is_compiled_only_from_case_data(container: Container) -> None:
    german = container.cases.get("ARI-FSP-001", "1.0")
    opening = container.patient_opening.build(german)
    first_spontaneous = next(
        fact.patient_phrase for fact in german.facts if fact.disclosure.value == "spontaneous"
    )
    assert opening == f"Guten Morgen. Ich heiße Sabine Keller. {first_spontaneous}"
    assert german.opening_statement not in opening or german.opening_statement == first_spontaneous


class EmptySidebandSocket:
    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration

    async def close(self) -> None:
        return None

    async def send(self, message: str) -> None:
        del message


class FakeCallsResponse:
    is_error = False
    status_code = 200
    text = "v=0\r\na=answer\r\n"
    headers: ClassVar[dict[str, str]] = {"location": "/v1/realtime/calls/rtc_test"}


class FakeHTTPClient:
    arguments: ClassVar[dict[str, object]] = {}

    def __init__(self, **kwargs: object) -> None:
        self.arguments["client"] = kwargs

    async def __aenter__(self) -> FakeHTTPClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def post(self, url: str, **kwargs: object) -> FakeCallsResponse:
        self.arguments.update(url=url, **kwargs)
        return FakeCallsResponse()


async def _fake_sideband_connect(url: str, **kwargs: object) -> Any:
    FakeHTTPClient.arguments.update(sideband_url=url, sideband_options=kwargs)
    return EmptySidebandSocket()


@pytest.mark.asyncio
async def test_openai_realtime_adapter_uses_semantic_vad_and_no_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime_module.httpx, "AsyncClient", FakeHTTPClient)
    monkeypatch.setattr(realtime_module, "connect", _fake_sideband_connect)
    context = ExecutionContext(
        session_id="session-1",
        learner_id="learner-1",
        operation="realtime_voice.connect",
        case_version="1.0",
        case_hash="case-hash",
    )
    simulation = RealtimeSimulationSpec(
        instructions="case instructions",
        language="en-US",
        transcription_context="medical interview",
        prompt_version="realtime-patient-v1",
        prompt_hash="prompt-hash",
    )
    engine = OpenAIRealtimeVoiceEngine("test-key")
    call = await engine.start_call(
        "v=0\r\n",
        context=context,
        simulation=simulation,
        profile=VoiceProfile.QUALITY,
        interaction_mode=InteractionMode.IMMERSIVE,
    )

    files = FakeHTTPClient.arguments["files"]
    assert isinstance(files, dict)
    session = json.loads(files["session"][1])
    assert session["model"] == "gpt-realtime-2.1"
    assert session["audio"]["output"]["voice"] == "marin"
    assert session["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad",
        "eagerness": "medium",
        "create_response": False,
        "interrupt_response": True,
    }
    assert session["reasoning"] == {"effort": "low"}
    assert session["tools"] == []
    assert FakeHTTPClient.arguments["sideband_url"].endswith("?call_id=rtc_test")
    assert call.call_id == "rtc_test"

    await engine.start_call(
        "v=0\r\n",
        context=context,
        simulation=simulation,
        profile=VoiceProfile.ECONOMY,
        interaction_mode=InteractionMode.GUIDED,
    )
    guided_files = FakeHTTPClient.arguments["files"]
    assert isinstance(guided_files, dict)
    guided_session = json.loads(guided_files["session"][1])
    assert guided_session["model"] == "gpt-realtime-2.1-mini"
    assert guided_session["audio"]["input"]["turn_detection"] is None


class RawSidebandSocket(EmptySidebandSocket):
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events = [json.dumps(event) for event in events]

    async def __anext__(self) -> str:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


@pytest.mark.asyncio
async def test_realtime_adapter_traces_cancelled_response_and_provider_error() -> None:
    context = ExecutionContext(
        session_id="session-1",
        operation="realtime_voice.connect",
        case_version="1.0",
        case_hash="case-hash",
    )
    socket = RawSidebandSocket(
        [
            {"type": "response.created", "response": {"id": "r1"}},
            {"type": "response.output_audio.delta", "response_id": "r1", "delta": "AA=="},
            {
                "type": "conversation.item.truncated",
                "item_id": "output-1",
                "audio_end_ms": 275,
            },
            {
                "type": "response.done",
                "response": {
                    "id": "r1",
                    "status": "cancelled",
                    "status_details": {"reason": "turn_detected"},
                    "usage": {},
                },
            },
            {
                "type": "error",
                "event_id": "event-1",
                "error": {"code": "server_error", "message": "temporary"},
            },
        ]
    )
    call = OpenAIRealtimeCall(
        socket=socket,  # type: ignore[arg-type]
        call_id="rtc_test",
        model="gpt-realtime-2.1",
        answer_sdp="v=0",
        execution=_execution("session-1", "realtime_voice.connect", "rtc_test"),
        context=context,
        prompt_version="realtime-patient-v1",
        prompt_hash="prompt-hash",
    )
    events = [event async for event in call.events()]

    assert events[0].type == "response_created"
    assert events[1].type == "patient_speech_started"
    interruption = next(event for event in events if event.type == "patient_interrupted")
    assert interruption.audio_end_ms == 275
    completed = next(event for event in events if event.type == "response_completed")
    assert completed.execution is not None
    assert completed.response_status == "cancelled"
    assert completed.execution.status is ExecutionStatus.FAILED
    assert completed.execution.error_code == "realtime_response_cancelled"
    provider_error = next(event for event in events if event.type == "error")
    assert provider_error.execution is not None
    assert provider_error.execution.error_code == "server_error"
    assert provider_error.execution.retryable is True
