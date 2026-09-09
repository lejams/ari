from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ari.api.dto import (
    CreateLearnerRequest,
    CreateSessionRequest,
    UpdateGoalRequest,
    VoiceFallbackRequest,
)
from ari.api.ownership import PROFILE_COOKIE, OwnershipMiddleware
from ari.api.practice import practice_router
from ari.api.public_session import public_session, public_turn
from ari.application.contracts import ExecutionContext, TranscriptionConfig
from ari.application.ports.realtime_voice import (
    RealtimeCall,
    RealtimeProviderEvent,
    RealtimeResponseKind,
)
from ari.application.schemas import ClientControlMessage
from ari.application.services.realtime import (
    RealtimeTurnAssembler,
    spoken_word_count,
)
from ari.application.services.realtime_fidelity import verify_response_fidelity
from ari.application.services.telemetry import (
    aggregate_voice_metrics,
    merge_voice_metric,
    new_voice_metric,
    utc_timestamp,
)
from ari.application.voice_stacks import VoiceStack, VoiceTransport
from ari.config import Settings, get_settings
from ari.container import Container, build_container
from ari.domain.errors import AriError, InvalidStateError, NotFoundError, ProviderError
from ari.domain.models import (
    ClockDomain,
    ConversationSession,
    ConversationTurn,
    ExecutionRecord,
    ExecutionStatus,
    InteractionMode,
    LearningGoal,
    LearningMode,
    MedicalCase,
    PatientOpening,
    PatientOpeningStatus,
    SessionStatus,
    TurnResponseState,
    VoiceMetricTransport,
    VoiceTurnMetricStatus,
    new_id,
)
from ari.infrastructure.persistence.identity import ProfileCredentials


def _payload(value: object) -> Any:
    return jsonable_encoder(value)


def _is_websocket_disconnect_runtime(exc: RuntimeError) -> bool:
    return "disconnect message has been received" in str(exc)


@dataclass(slots=True)
class VoiceLifecycle:
    end_requested: asyncio.Event = field(default_factory=asyncio.Event)
    drained: asyncio.Event = field(default_factory=asyncio.Event)
    provider_start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    manual_turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    manual_recording: bool = False
    guided_phase: str = "opening"


def _public_case(case: MedicalCase) -> dict[str, object]:
    return {
        "id": case.id,
        "version": case.version,
        "mode": case.mode.value,
        "validation_status": case.validation_status,
        "title": case.title,
        "language": case.language,
        "public_summary": case.public_summary,
        "difficulty": case.difficulty,
        "educational_target": {
            "exam": case.educational_target.exam,
            "phase": case.educational_target.phase,
            "duration_minutes": case.educational_target.duration_minutes,
        },
        "rubric_version": case.rubric_version,
        "training_snapshot": dict(case.training_snapshot),
    }


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    services = container or build_container(settings or get_settings())
    if services.settings.environment == "production" and services.settings.enable_mvp_demos:
        raise InvalidStateError("Synthetic MVP demos are forbidden in production")
    voice_session_lock = asyncio.Lock()
    voice_lifecycles: dict[str, VoiceLifecycle] = {}
    realtime_calls: dict[str, RealtimeCall] = {}
    realtime_ready: dict[str, asyncio.Event] = {}
    realtime_lock = asyncio.Lock()
    analysis_locks: dict[str, asyncio.Lock] = {}

    def voice_stack_for(session: ConversationSession) -> VoiceStack:
        return services.voice_stacks.resolve_persisted(
            session.voice_stack_id,
            session.voice_stack_version,
            session.voice_stack_config,
        )

    def public_stack(stack: VoiceStack) -> dict[str, object]:
        return {
            "id": stack.id,
            "version": stack.version,
            "transport": stack.transport.value,
            "models": dict(stack.models),
            "available": services.voice_stack_available(stack),
        }

    def session_payload(session: ConversationSession) -> dict[str, object]:
        payload = public_session(session)
        snapshot = dict(session.voice_stack_config)
        payload["voice_transport"] = snapshot.get("transport", "unknown")
        payload["voice_models"] = payload["voice_stack_config"]["models"]
        try:
            stack = voice_stack_for(session)
        except AriError:
            payload["voice_stack_available"] = False
        else:
            payload["voice_stack_available"] = services.voice_stack_available(stack)
        return payload

    def persist_client_playback_observation(
        turn: ConversationTurn, control: ClientControlMessage
    ) -> None:
        metric = services.repository.get_voice_turn_metric(turn.id)
        if metric is None:
            return
        durations = {
            "speech_end_to_audio_started_ms": control.speech_end_to_audio_started_ms,
            "audio_sent_to_playback_started_ms": (control.audio_sent_to_playback_started_ms),
        }
        available = {key: value for key, value in durations.items() if value is not None}
        domains = {key: ClockDomain.BROWSER for key in available}
        wall = (
            {"playback_started_observed_at": utc_timestamp()}
            if control.type == "audio.playback_started"
            else {"playback_completed_observed_at": utc_timestamp()}
        )
        services.repository.record_voice_turn_metric(
            merge_voice_metric(
                metric,
                delivery_status=turn.delivery_status,
                durations=available,
                clock_domains=domains,
                wall_timestamps_utc=wall,
            )
        )

    def sync_metric_delivery_status(session_id: str) -> None:
        current = services.repository.get_session(session_id)
        turns = {turn.id: turn for turn in current.turns}
        for metric in services.repository.list_voice_turn_metrics(session_id):
            turn = turns.get(metric.turn_id)
            if turn is not None and turn.delivery_status is not metric.delivery_status:
                services.repository.record_voice_turn_metric(
                    merge_voice_metric(metric, delivery_status=turn.delivery_status)
                )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if services.settings.auto_create_schema:
            if services.settings.environment == "production":
                raise RuntimeError(
                    "ARI_AUTO_CREATE_SCHEMA cannot be enabled in production; run Alembic"
                )
            services.repository.initialize_schema()
        yield
        for call in tuple(realtime_calls.values()):
            with suppress(Exception):
                await call.close()

    app = FastAPI(title="ARI FSP POC", version="0.1.0", lifespan=lifespan)
    app.state.container = services
    app.include_router(practice_router(services))
    credentials = ProfileCredentials(services.repository.engine)
    app.add_middleware(
        OwnershipMiddleware,
        credentials=credentials,
        origin=services.settings.frontend_origin,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[services.settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AriError)
    async def ari_error(_: Request, exc: AriError) -> JSONResponse:
        if isinstance(exc, ProviderError):
            status = 502
        elif isinstance(exc, NotFoundError):
            status = 404
        else:
            status = 400
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "provider_mode": services.settings.provider_mode,
            "voice_transport": services.settings.voice_transport,
            "technical_test_enabled": services.settings.enable_english_technical_test,
            "voice_stacks": [public_stack(stack) for stack in services.voice_stacks.list()],
        }

    @app.get("/api/voice-stacks")
    async def list_voice_stacks() -> list[dict[str, object]]:
        return [public_stack(stack) for stack in services.voice_stacks.list()]

    @app.get("/api/technical/voice-metrics")
    async def technical_voice_metrics(
        request: Request,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> dict[str, object]:
        if services.settings.environment == "production":
            raise HTTPException(status_code=404, detail="Not found")
        owned_sessions = {s.id for s in services.repository.list_sessions(request.state.learner_id)}
        return aggregate_voice_metrics(
            tuple(
                m
                for m in services.repository.list_voice_turn_metrics()
                if m.session_id in owned_sessions
            ),
            window_start=window_start,
            window_end=window_end,
        )

    @app.get("/api/cases")
    async def list_cases(approved_only: bool = False) -> list[dict[str, object]]:
        return [
            _public_case(case)
            for case in services.cases.list()
            if case.validation_status == "published"
            or (services.settings.environment == "test" and not approved_only)
        ]

    @app.post("/api/learners", status_code=201)
    async def create_learner(
        body: CreateLearnerRequest,
        request: Request,
        response: Response,
    ) -> Any:
        if request.state.learner_id:
            return _payload(services.repository.get_learner(request.state.learner_id))
        learner = services.orchestrator.create_learner(body.target_cefr)
        token = credentials.issue(learner.id)
        response.set_cookie(
            PROFILE_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            max_age=365 * 24 * 3600,
            secure=services.settings.environment == "production",
        )
        return _payload(learner)

    @app.get("/api/profile")
    async def current_profile(request: Request) -> Any:
        return _payload(services.repository.get_learner(request.state.learner_id))

    @app.delete("/api/profile", status_code=204)
    async def disconnect_profile(request: Request) -> Response:
        credentials.revoke(request.cookies[PROFILE_COOKIE])
        response = Response(status_code=204)
        response.delete_cookie(
            PROFILE_COOKIE,
            httponly=True,
            samesite="strict",
            secure=services.settings.environment == "production",
        )
        return response

    @app.get("/api/learners/{learner_id}/goal")
    async def get_goal(learner_id: str) -> Any:
        return _payload(services.repository.get_learner(learner_id).goal)

    @app.patch("/api/learners/{learner_id}/goal")
    async def update_goal(learner_id: str, body: UpdateGoalRequest) -> Any:
        goal = LearningGoal(body.target_exam, body.target_cefr, body.rubric_version)
        return _payload(services.orchestrator.update_goal(learner_id, goal).goal)

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: CreateSessionRequest, request: Request) -> Any:
        if body.learner_id != request.state.learner_id:
            raise HTTPException(status_code=404, detail="Not found")
        request_hash = hashlib.sha256(
            body.model_dump_json(exclude={"request_id"}).encode(),
        ).hexdigest()
        if body.request_id:
            existing = services.repository.find_session_request(body.learner_id, body.request_id)
            if existing:
                if existing.start_request_hash != request_hash:
                    raise InvalidStateError("Clé de démarrage réutilisée avec une autre demande")
                return session_payload(existing)
        selected = services.cases.get(
            body.case_id,
            body.case_version,
            scenario_id=body.scenario_id,
            scenario_version=body.scenario_version,
        )
        if selected.validation_status != "published" and (
            services.settings.environment != "test" or body.learning_mode is not None
        ):
            raise InvalidStateError("Aucun scénario vocal approuvé disponible pour ce choix")
        handle = services.practice_lifecycle.start_practice(
            "patient_voice",
            body.learner_id,
            case_id=body.case_id,
            case_version=body.case_version,
            voice_profile=body.voice_profile,
            interaction_mode=body.interaction_mode,
            voice_stack_id=body.voice_stack_id,
            scenario_id=body.scenario_id,
            scenario_version=body.scenario_version,
            learning_mode=body.learning_mode,
            request_id=body.request_id,
            request_hash=request_hash if body.request_id else None,
        )
        services.practice_lifecycle.get_projection(handle)
        return session_payload(services.repository.get_session(handle.id))

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> Any:
        handle = services.practice_lifecycle.voice_handle(session_id)
        services.practice_lifecycle.get_projection(handle)
        services.practice_lifecycle.get_feedback(handle)
        return session_payload(services.repository.get_session(session_id))

    @app.post("/api/sessions/{session_id}/end")
    async def end_session(session_id: str) -> Any:
        session = services.repository.get_session(session_id)
        lifecycle: VoiceLifecycle | None = None
        if session.status is SessionStatus.ACTIVE:
            async with voice_session_lock:
                lifecycle = voice_lifecycles.get(session_id)
                if lifecycle is not None:
                    lifecycle.end_requested.set()
        if lifecycle is not None and not lifecycle.drained.is_set():
            try:
                async with asyncio.timeout(65):
                    await lifecycle.drained.wait()
            except TimeoutError as exc:
                raise InvalidStateError("Voice session is still being finalized") from exc
        async with analysis_locks.setdefault(session_id, asyncio.Lock()):
            handle = services.practice_lifecycle.voice_handle(session_id)
            await services.practice_lifecycle.end_practice(handle)
            return session_payload(services.repository.get_session(session_id))

    @app.post("/api/sessions/{session_id}/analysis/retry")
    async def retry_analysis(session_id: str) -> Any:
        session = services.repository.get_session(session_id)
        if session.status not in {
            SessionStatus.ANALYSIS_PENDING,
            SessionStatus.ANALYSIS_FAILED,
            SessionStatus.COMPLETED,
        }:
            raise InvalidStateError(f"Cannot retry analysis for a {session.status} session")
        async with analysis_locks.setdefault(session_id, asyncio.Lock()):
            handle = services.practice_lifecycle.voice_handle(session_id)
            await services.practice_lifecycle.end_practice(handle)
            services.practice_lifecycle.get_feedback(handle)
            return session_payload(services.repository.get_session(session_id))

    @app.get("/api/learners/{learner_id}/sessions")
    async def list_sessions(learner_id: str) -> Any:
        return [session_payload(item) for item in services.repository.list_sessions(learner_id)]

    @app.post("/api/sessions/{session_id}/voice/fallback")
    async def accept_voice_fallback(session_id: str, body: VoiceFallbackRequest) -> Any:
        current = services.repository.get_session(session_id)
        if current.voice_stack_id == body.target_voice_stack_id:
            if any(
                item.from_stack_id == body.failed_voice_stack_id
                and item.to_stack_id == body.target_voice_stack_id
                for item in current.voice_stack_transitions
            ):
                return session_payload(current)
            raise InvalidStateError("Session already has a different stack history")
        stack = voice_stack_for(current)
        if stack.id != body.failed_voice_stack_id or stack.transport is not VoiceTransport.REALTIME:
            raise InvalidStateError("Fallback source is not the persisted Realtime stack")
        target = services.voice_stacks.get(body.target_voice_stack_id)
        offered = services.voice_stacks.pipeline_alternative_for(current.interaction_mode)
        if target.id != offered.id or not services.voice_stack_available(target):
            raise InvalidStateError("Requested fallback is not the available alternative")
        return session_payload(
            services.repository.switch_voice_stack_before_first_turn(
                session_id,
                expected_stack_id=stack.id,
                target_stack_id=target.id,
                target_stack_version=target.version,
                target_stack_config=target.snapshot(),
                reason="realtime_connection_failed",
            )
        )

    @app.get("/api/sessions/{session_id}/vocabulary-hints")
    async def get_vocabulary_hints(session_id: str) -> Any:
        session = services.repository.get_session(session_id)
        if (
            session.learning_mode is LearningMode.EXAM
            and session.status is not SessionStatus.COMPLETED
        ):
            raise NotFoundError("Aide indisponible pendant Exam")
        asset = (
            services.cases.vocabulary_for_session(session)
            if session.training_snapshot
            else services.vocabulary_hints.get_for_case(session.case_id, session.case_version)
        )
        if asset is None:
            raise NotFoundError("No vocabulary hints are available for this case")
        return _payload(
            {
                "asset_id": asset.id,
                "version": asset.version,
                "language": asset.language,
                "translation_language": asset.translation_language,
                "items": asset.hints,
            }
        )

    @app.post("/api/sessions/{session_id}/vocabulary-hints/{hint_id}/use")
    async def use_vocabulary_hint(session_id: str, hint_id: str) -> Any:
        session = services.repository.get_session(session_id)
        if (
            session.learning_mode is LearningMode.EXAM
            and session.status is not SessionStatus.COMPLETED
        ):
            raise NotFoundError("Aide indisponible pendant Exam")
        asset = (
            services.cases.vocabulary_for_session(session)
            if session.training_snapshot
            else services.vocabulary_hints.get_for_case(session.case_id, session.case_version)
        )
        if asset is None:
            raise NotFoundError("No vocabulary hints are available for this case")
        if hint_id not in {item.id for item in asset.hints}:
            raise NotFoundError(f"Vocabulary hint {hint_id} was not found")
        return _payload(
            services.repository.record_vocabulary_hint_usage(session_id, hint_id, asset.version)
        )

    @app.post("/api/sessions/{session_id}/voice/realtime")
    async def start_realtime_voice(session_id: str, request: Request) -> Response:
        current_session = services.repository.get_session(session_id)
        stack = voice_stack_for(current_session)
        realtime_engine = services.realtime_voice_for(stack.id)
        if stack.transport is not VoiceTransport.REALTIME or realtime_engine is None:
            raise InvalidStateError("Realtime voice is unavailable in the current provider mode")
        if not request.headers.get("content-type", "").startswith("application/sdp"):
            return Response("Expected application/sdp", status_code=415, media_type="text/plain")
        offer_sdp = (await request.body()).decode("utf-8").strip()
        if not offer_sdp:
            return Response("SDP offer cannot be empty", status_code=422, media_type="text/plain")
        if not offer_sdp.startswith("v=0"):
            return Response("Invalid SDP offer", status_code=422, media_type="text/plain")
        offer_sdp = offer_sdp.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n"
        async with voice_session_lock:
            lifecycle = voice_lifecycles.get(session_id)
            if lifecycle is None:
                raise InvalidStateError("The voice control connection is not active")
            if lifecycle.end_requested.is_set():
                raise InvalidStateError("The voice session is ending")
        session = services.repository.get_session(session_id)
        services.practice_lifecycle.resume_practice(
            services.practice_lifecycle.voice_handle(session_id)
        )
        session = services.repository.get_session(session_id)
        case = services.orchestrator._case_for_session(session)
        # Realtime is a transport only. Patient simulation is performed by the
        # application facade and the provider receives no case facts.
        simulation = services.realtime_simulation.build_transport(case)
        context = ExecutionContext(
            session_id=session.id,
            learner_id=session.learner_id,
            operation="realtime_voice.connect",
            case_version=case.version,
            case_hash=case.content_hash,
            prompt_version=simulation.prompt_version,
            prompt_hash=simulation.prompt_hash,
        )
        async with lifecycle.provider_start_lock:
            if lifecycle.end_requested.is_set():
                raise InvalidStateError("The voice session is ending")
            async with realtime_lock:
                if session_id in realtime_calls:
                    raise InvalidStateError("A Realtime call already exists for this session")
                try:
                    call = await realtime_engine.start_call(
                        offer_sdp,
                        context=context,
                        simulation=simulation,
                        profile=session.voice_profile,
                        interaction_mode=session.interaction_mode,
                    )
                except ProviderError as exc:
                    services.repository.record_execution(cast(ExecutionRecord, exc.execution))
                    alternative = services.voice_stacks.pipeline_alternative_for(
                        session.interaction_mode
                    )
                    return JSONResponse(
                        status_code=502,
                        content={
                            "detail": str(exc),
                            "fallback": {
                                "failed_voice_stack_id": stack.id,
                                "target_voice_stack_id": alternative.id,
                                "target_voice_stack_version": alternative.version,
                                "transport": alternative.transport.value,
                                "models": dict(alternative.models),
                                "available": services.voice_stack_available(alternative),
                            },
                        },
                    )
                services.repository.record_execution(call.execution)
                realtime_calls[session_id] = call
                realtime_ready.setdefault(session_id, asyncio.Event()).set()
        return Response(content=call.answer_sdp, media_type="application/sdp")

    async def handle_realtime_socket(
        websocket: WebSocket,
        session_id: str,
        lifecycle: VoiceLifecycle,
        selected_stack: VoiceStack,
    ) -> None:
        services.practice_lifecycle.resume_practice(
            services.practice_lifecycle.voice_handle(session_id)
        )
        session = services.repository.get_session(session_id)
        case = services.orchestrator._case_for_session(session)
        simulation_trace = services.realtime_simulation.build_transport(case)
        assembler = RealtimeTurnAssembler()
        send_lock = asyncio.Lock()
        processing_lock = asyncio.Lock()
        audit_tasks: set[asyncio.Task[None]] = set()
        response_started_at: dict[str, float] = {}
        speech_stopped_at: dict[str, float] = {}
        transcript_final_at: dict[str, float] = {}
        realtime_wall_timestamps: dict[str, dict[str, str]] = {}
        response_text: dict[str, str] = {}
        opening_transcripts: dict[str, str] = {}
        length_limited_responses: set[str] = set()
        length_limit_attempted: set[str] = set()
        response_limit_tasks: dict[str, asyncio.Task[None]] = {}
        active_response_id: str | None = None
        pending_response_interruption = False
        last_speech_stopped_at: float | None = None
        timed_facts_released = asyncio.Event()
        response_idle = asyncio.Event()
        response_idle.set()
        provider_response_terminal = asyncio.Event()
        provider_response_terminal.set()
        response_create_lock = asyncio.Lock()
        response_create_tasks: set[asyncio.Task[None]] = set()
        user_turn_ready = asyncio.Event()
        pending_turns = 0
        seen_speech_items: set[str] = set()
        anonymous_turn_ids: deque[str] = deque()
        pending_turns_by_input: dict[str, ConversationTurn] = {}
        pending_anonymous_turns: deque[ConversationTurn] = deque()
        finalized = asyncio.Event()
        opening_text = services.patient_opening.build(case)

        async def send(event_type: str, **data: object) -> bool:
            try:
                async with send_lock:
                    await websocket.send_json({"type": event_type, "data": data})
            except (RuntimeError, WebSocketDisconnect):
                return False
            return True

        def command_failure(
            call: RealtimeCall, operation: str, error: Exception
        ) -> ExecutionRecord:
            return ExecutionRecord(
                id=new_id(),
                session_id=session_id,
                operation=operation,
                provider=call.provider,
                model=call.model,
                status=ExecutionStatus.FAILED,
                prompt_version=simulation_trace.prompt_version,
                prompt_hash=simulation_trace.prompt_hash,
                case_version=case.version,
                case_hash=case.content_hash,
                latency_ms=0,
                usage={},
                error_code=type(error).__name__,
                error_message=str(error)[:1000],
                retryable=True,
            )

        async def audit_turn(turn: ConversationTurn) -> None:
            try:
                current = services.repository.get_session(session_id)
                outcome = await services.grounding_auditor.audit(current, case, turn)
                services.repository.record_execution(outcome.execution)
                services.repository.save_grounding_audit(outcome.audit)
                trusted_fact_ids = (
                    ()
                    if (
                        turn.interrupted
                        or turn.provider_response_status != "completed"
                        or outcome.audit.severity in {"high", "critical"}
                    )
                    else outcome.audit.supported_fact_ids
                )
                services.repository.update_turn_selected_fact_ids(turn.id, trusted_fact_ids)
                # Grounding audits are internal and never sent to the browser.
            except ProviderError as exc:
                services.repository.record_execution(cast(ExecutionRecord, exc.execution))
                with suppress(Exception):
                    await send("grounding.audit_failed", turn_id=turn.id)
            except Exception:
                with suppress(Exception):
                    await send("grounding.audit_failed", turn_id=turn.id)

        async def persist_user_transcript(
            text: str, provider_input_item_id: str | None
        ) -> ConversationTurn | None:
            clean_text = text.strip()
            if not clean_text:
                return None
            async with processing_lock:
                if provider_input_item_id:
                    existing = services.repository.get_turn_by_provider_input(
                        session_id, provider_input_item_id
                    )
                    if existing is not None:
                        return existing
                current = services.repository.get_session(session_id)
                turn = ConversationTurn(
                    id=new_id(),
                    session_id=session_id,
                    sequence=len(current.turns) + 1,
                    user_text=clean_text,
                    patient_text="",
                    revealed_fact_ids=(),
                    provider_input_item_id=provider_input_item_id,
                    provider_response_status="awaiting_response",
                )
                turn = services.repository.append_turn_idempotent(turn)
                if provider_input_item_id is None:
                    anonymous_turn_ids.append(turn.id)
                    pending_anonymous_turns.append(turn)
                else:
                    pending_turns_by_input[provider_input_item_id] = turn
                await send("turn.persisted", turn=public_turn(turn))
                return turn

        async def persist_completed(completed: object) -> None:
            from ari.application.services.realtime import CompletedRealtimeTurn

            item = cast(CompletedRealtimeTurn, completed)
            response_completed_at = time.perf_counter()
            if services.repository.get_turn_by_provider_response(session_id, item.response_id):
                return
            async with processing_lock:
                turn = (
                    services.repository.get_turn_by_provider_input(session_id, item.input_item_id)
                    if item.input_item_id
                    else None
                )
                if turn is None and anonymous_turn_ids:
                    anonymous_id = anonymous_turn_ids.popleft()
                    turn = next(
                        (
                            candidate
                            for candidate in services.repository.get_session(session_id).turns
                            if candidate.id == anonymous_id
                        ),
                        None,
                    )
                if turn is None:
                    current = services.repository.get_session(session_id)
                    turn = ConversationTurn(
                        id=new_id(),
                        session_id=session_id,
                        sequence=len(current.turns) + 1,
                        user_text=item.user_text,
                        patient_text="",
                        revealed_fact_ids=(),
                        provider_input_item_id=item.input_item_id,
                        provider_response_status="awaiting_response",
                    )
                    turn = services.repository.append_turn_idempotent(turn)
                    await send("turn.persisted", turn=public_turn(turn))
                effective_status = (
                    "length_limited"
                    if item.response_id in length_limited_responses
                    else item.response_status
                )
                interrupted = item.interrupted or effective_status == "length_limited"
                canonical = turn.canonical_response
                canonical_text = (
                    str(canonical.get("text", "")).strip() if isinstance(canonical, Mapping) else ""
                )
                fidelity = verify_response_fidelity(canonical_text, item.patient_text)
                turn = services.repository.finalize_transport_response(
                    turn.id,
                    provider_response_id=item.response_id,
                    provider_response_status=effective_status,
                    canonical_response=canonical,
                    observed_response_text=None if interrupted else item.patient_text,
                    fidelity_matches=fidelity.matches,
                    interrupted=interrupted,
                    interruption_audio_end_ms=item.interruption_audio_end_ms,
                )
                if (
                    not interrupted
                    and fidelity.matches
                    and effective_status == "completed"
                    and canonical_text
                ):
                    turn = services.repository.begin_audio_stream(
                        session_id, turn.id, item.response_id
                    )
                    turn = services.repository.mark_audio_sent(
                        session_id, turn.id, item.response_id, 1
                    )
                observed_response_ms = int(
                    1000
                    * max(
                        0.0,
                        time.perf_counter()
                        - response_started_at.get(item.response_id, time.perf_counter()),
                    )
                )
                word_count = spoken_word_count(turn.patient_text)
                if item.execution is not None:
                    usage = {
                        **item.execution.usage,
                        "spoken_word_count": word_count,
                        "observed_response_ms": observed_response_ms,
                        "response_kind": RealtimeResponseKind.PATIENT_ANSWER.value,
                    }
                    services.repository.record_execution(
                        replace(item.execution, turn_id=turn.id, usage=usage)
                    )
                input_key = item.input_item_id or "anonymous"
                speech_end = speech_stopped_at.get(input_key)
                if speech_end is None:
                    speech_end = last_speech_stopped_at
                transcript_end = transcript_final_at.get(input_key)

                def elapsed(start: float | None, end: float | None) -> int | None:
                    if start is None or end is None or end < start:
                        return None
                    return int((end - start) * 1000)

                realtime_durations = {
                    "speech_end_to_transcript_final_ms": elapsed(speech_end, transcript_end),
                    # Realtime combines speech generation; no separate LLM stage is exposed.
                    "llm_total_ms": None,
                    # WebRTC audio bypasses this server; sideband arrival is not an audio send.
                    "speech_end_to_first_audio_sent_ms": None,
                    "turn_total_ms": elapsed(speech_end, response_completed_at),
                }
                available_durations = {
                    key: value for key, value in realtime_durations.items() if value is not None
                }
                wall = {
                    **realtime_wall_timestamps.get("anonymous", {}),
                    **realtime_wall_timestamps.get(input_key, {}),
                    **realtime_wall_timestamps.get(item.response_id, {}),
                    "turn_ended_at": utc_timestamp(),
                }
                services.repository.record_voice_turn_metric(
                    new_voice_metric(
                        services.repository.get_session(session_id),
                        turn,
                        transport=VoiceMetricTransport.REALTIME,
                        application_version=services.settings.application_version,
                        durations=realtime_durations,
                        clock_domains={key: ClockDomain.SERVER for key in available_durations},
                        wall_timestamps_utc=wall,
                        executions=(item.execution,) if item.execution is not None else (),
                        status=(
                            VoiceTurnMetricStatus.FAILED
                            if (
                                interrupted
                                or effective_status != "completed"
                                or not fidelity.matches
                            )
                            else VoiceTurnMetricStatus.COMPLETED
                        ),
                        error_count=(
                            1 if effective_status != "completed" or not fidelity.matches else 0
                        ),
                    )
                )
                await send(
                    "turn.completed",
                    turn=public_turn(turn),
                    telemetry={
                        "transport": "webrtc",
                        "response_start_ms": int(
                            1000
                            * max(
                                0.0,
                                response_started_at.get(item.response_id, time.perf_counter())
                                - (last_speech_stopped_at or time.perf_counter()),
                            )
                        ),
                        "model": realtime_calls[session_id].model,
                        "usage": item.usage,
                        "estimated_cost_usd": (
                            item.execution.estimated_cost_usd if item.execution else None
                        ),
                        "interrupted": item.interrupted,
                        "spoken_word_count": word_count,
                        "observed_response_ms": observed_response_ms,
                        "response_kind": RealtimeResponseKind.PATIENT_ANSWER.value,
                    },
                )

        audit_scheduled_turn_ids: set[str] = set()

        async def acknowledge_realtime_audio(control: ClientControlMessage) -> None:
            if control.type == "audio.playback_completed":
                await send(
                    "audio.ack_rejected",
                    turn_id=control.turn_id,
                    message="WebRTC cannot prove per-response playback completion",
                )
                return
            try:
                turn = services.repository.confirm_audio_started(
                    session_id,
                    cast(str, control.turn_id),
                    cast(str, control.audio_stream_id),
                    provider_response_id=control.response_id,
                    last_index=cast(int, control.last_index),
                )
            except AriError as exc:
                await send("audio.ack_rejected", turn_id=control.turn_id, message=str(exc))
                return
            persist_client_playback_observation(turn, control)
            await send("turn.audio_started", turn=public_turn(turn))
            if (
                turn.id not in audit_scheduled_turn_ids
                and turn.patient_text
                and turn.canonical_response is None
                and not turn.interrupted
            ):
                audit_scheduled_turn_ids.add(turn.id)
                task = asyncio.create_task(audit_turn(turn))
                audit_tasks.add(task)
                task.add_done_callback(audit_tasks.discard)

        async def limit_response(response_id: str) -> None:
            await asyncio.sleep(12)
            if response_id != active_response_id or finalized.is_set():
                return
            await interrupt_for_limit(response_id)

        async def interrupt_for_limit(response_id: str) -> None:
            if response_id in length_limit_attempted:
                return
            length_limit_attempted.add(response_id)
            call = realtime_calls.get(session_id)
            if call is None:
                return
            try:
                await call.interrupt_response()
            except ProviderError as exc:
                services.repository.record_execution(cast(ExecutionRecord, exc.execution))
                await send(
                    "response.limit_failed",
                    response_id=response_id,
                    message=str(exc),
                )
                return
            except Exception as exc:
                services.repository.record_execution(
                    command_failure(call, "realtime_voice.interrupt", exc)
                )
                await send(
                    "response.limit_failed",
                    response_id=response_id,
                    message=str(exc),
                )
                return
            length_limited_responses.add(response_id)
            await send(
                "patient.interrupted",
                response_id=response_id,
                reason="length_limited",
            )

        async def pump_events() -> None:
            nonlocal active_response_id, last_speech_stopped_at, pending_turns
            nonlocal pending_response_interruption
            try:
                ready = realtime_ready.setdefault(session_id, asyncio.Event())
                await asyncio.wait_for(ready.wait(), timeout=30)
                call = realtime_calls[session_id]
                await send(
                    "realtime.connected",
                    call_id=call.call_id,
                    model=call.model,
                    voice_profile=session.voice_profile.value,
                    interaction_mode=session.interaction_mode.value,
                )
                current = services.repository.get_session(session_id)
                should_open = current.patient_opening is None and not current.turns
                if should_open:
                    opening = services.repository.save_patient_opening(
                        PatientOpening(
                            session_id=session_id,
                            text=opening_text,
                            status=PatientOpeningStatus.PENDING,
                        )
                    )
                    await send("patient.opening_started", text=opening.text)
                    try:
                        await call.create_response(
                            kind=RealtimeResponseKind.OPENING,
                            exact_text=opening.text,
                        )
                    except ProviderError as exc:
                        services.repository.record_execution(cast(ExecutionRecord, exc.execution))
                        services.repository.save_patient_opening(
                            replace(opening, status=PatientOpeningStatus.FAILED)
                        )
                        await send("patient.opening_failed", text=opening.text)
                        user_turn_ready.set()
                        lifecycle.guided_phase = "ready"
                        await send("user.turn.ready")
                    except Exception as exc:
                        services.repository.record_execution(
                            command_failure(call, "realtime_voice.opening", exc)
                        )
                        services.repository.save_patient_opening(
                            replace(opening, status=PatientOpeningStatus.FAILED)
                        )
                        await send("patient.opening_failed", text=opening.text)
                        user_turn_ready.set()
                        lifecycle.guided_phase = "ready"
                        await send("user.turn.ready")
                else:
                    if (
                        current.patient_opening is not None
                        and current.patient_opening.status is PatientOpeningStatus.PENDING
                    ):
                        failed_opening = services.repository.save_patient_opening(
                            replace(
                                current.patient_opening,
                                status=PatientOpeningStatus.FAILED,
                            )
                        )
                        await send("patient.opening_failed", text=failed_opening.text)
                    user_turn_ready.set()
                    lifecycle.guided_phase = "ready"
                    await send("user.turn.ready")
                async for event in call.events():
                    if event.response_kind is RealtimeResponseKind.OPENING:
                        if event.type == "patient_speech_started" and event.response_id:
                            response_started_at[event.response_id] = time.perf_counter()
                        elif event.type == "patient_transcript_delta":
                            if event.response_id:
                                opening_transcripts[event.response_id] = opening_transcripts.get(
                                    event.response_id, ""
                                ) + (event.text or "")
                            await send("patient.transcript_delta", text=event.text or "")
                        elif event.type == "patient_transcript_final" and event.response_id:
                            opening_transcripts[event.response_id] = (event.text or "").strip()
                        elif event.type == "response_completed":
                            stored_opening = services.repository.get_session(
                                session_id
                            ).patient_opening
                            if stored_opening is None:
                                continue
                            if (
                                stored_opening.status
                                in {PatientOpeningStatus.COMPLETED, PatientOpeningStatus.FAILED}
                                and stored_opening.provider_response_id == event.response_id
                            ):
                                continue
                            spoken_text = opening_transcripts.get(
                                event.response_id or "", ""
                            ).strip()
                            provider_succeeded = event.response_status in {
                                None,
                                "completed",
                                "succeeded",
                            }
                            exact_match = spoken_text == stored_opening.text
                            succeeded = provider_succeeded and exact_match
                            status = (
                                PatientOpeningStatus.COMPLETED
                                if succeeded
                                else PatientOpeningStatus.FAILED
                            )
                            stored_opening = services.repository.save_patient_opening(
                                replace(
                                    stored_opening,
                                    status=status,
                                    spoken_text=spoken_text or None,
                                    provider_response_id=event.response_id,
                                )
                            )
                            if event.execution is not None:
                                started = response_started_at.get(
                                    event.response_id or "", time.perf_counter()
                                )
                                services.repository.record_execution(
                                    replace(
                                        event.execution,
                                        usage={
                                            **event.execution.usage,
                                            "spoken_word_count": spoken_word_count(
                                                stored_opening.spoken_text or ""
                                            ),
                                            "observed_response_ms": int(
                                                1000 * max(0.0, time.perf_counter() - started)
                                            ),
                                            "response_kind": RealtimeResponseKind.OPENING.value,
                                            "planned_text": stored_opening.text,
                                            "opening_exact_match": exact_match,
                                        },
                                    )
                                )
                            await send(
                                (
                                    "patient.opening_completed"
                                    if succeeded
                                    else "patient.opening_failed"
                                ),
                                text=stored_opening.text,
                                spoken_text=stored_opening.spoken_text,
                                response_id=event.response_id,
                            )
                            user_turn_ready.set()
                            lifecycle.guided_phase = "ready"
                            await send("user.turn.ready")
                        elif event.type == "error":
                            error_opening = services.repository.get_session(
                                session_id
                            ).patient_opening
                            if error_opening is not None:
                                services.repository.save_patient_opening(
                                    replace(
                                        error_opening,
                                        status=PatientOpeningStatus.FAILED,
                                    )
                                )
                            if event.execution is not None:
                                services.repository.record_execution(event.execution)
                            await send("patient.opening_failed", text=opening_text)
                            user_turn_ready.set()
                            lifecycle.guided_phase = "ready"
                            await send("user.turn.ready")
                        continue
                    if event.type == "user_transcript_final" and event.text:
                        await persist_user_transcript(event.text, event.input_item_id)
                    completed = assembler.handle(event)
                    if event.type == "user_speech_started":
                        if session.interaction_mode is InteractionMode.IMMERSIVE and (
                            event.input_item_id is None
                            or event.input_item_id not in seen_speech_items
                        ):
                            if event.input_item_id is not None:
                                seen_speech_items.add(event.input_item_id)
                            pending_turns += 1
                            response_idle.clear()
                        await send("user.speech_started", item_id=event.input_item_id)
                        input_key = event.input_item_id or "anonymous"
                        realtime_wall_timestamps.setdefault(input_key, {})["speech_started_at"] = (
                            utc_timestamp()
                        )
                        if event.response_id:
                            await send("patient.interrupted", response_id=event.response_id)
                    elif event.type == "user_speech_stopped":
                        last_speech_stopped_at = time.perf_counter()
                        input_key = event.input_item_id or "anonymous"
                        speech_stopped_at[input_key] = last_speech_stopped_at
                        realtime_wall_timestamps.setdefault(input_key, {})["speech_ended_at"] = (
                            utc_timestamp()
                        )
                    elif event.type == "user_transcript_delta":
                        await send("user.transcript_delta", text=event.text or "")
                    elif event.type == "user_transcript_final":
                        input_key = event.input_item_id or "anonymous"
                        transcript_final_at[input_key] = time.perf_counter()
                        realtime_wall_timestamps.setdefault(input_key, {})[
                            "transcript_final_at"
                        ] = utc_timestamp()
                        await send("user.transcript_final", text=event.text or "")
                        # Disclosure and case-time updates belong to the
                        # application PatientSimulator, never to the transport.
                        if lifecycle.end_requested.is_set():
                            pending_turns = max(0, pending_turns - 1)
                            if pending_turns == 0:
                                response_idle.set()
                        else:
                            reserved_for_response = (
                                pending_turns_by_input.get(event.input_item_id)
                                if event.input_item_id is not None
                                else (
                                    pending_anonymous_turns.popleft()
                                    if pending_anonymous_turns
                                    else None
                                )
                            )
                            task = asyncio.create_task(
                                request_patient_response(
                                    call,
                                    reserved_for_response,
                                )
                            )
                            response_create_tasks.add(task)
                            task.add_done_callback(response_create_tasks.discard)
                    elif event.type == "response_created":
                        if event.response_id:
                            realtime_wall_timestamps.setdefault(event.response_id, {})[
                                "response_created_observed_at"
                            ] = utc_timestamp()
                            active_response_id = event.response_id
                            if pending_response_interruption:
                                assembler.handle(
                                    RealtimeProviderEvent(
                                        "patient_interrupted",
                                        response_id=event.response_id,
                                    )
                                )
                                pending_response_interruption = False
                            response_limit_tasks[event.response_id] = asyncio.create_task(
                                limit_response(event.response_id)
                            )
                    elif event.type == "patient_speech_started":
                        if event.response_id:
                            first_audio = time.perf_counter()
                            response_started_at[event.response_id] = first_audio
                            realtime_wall_timestamps.setdefault(event.response_id, {})[
                                "provider_audio_started_observed_at"
                            ] = utc_timestamp()
                            active_response_id = event.response_id
                            if event.response_id not in response_limit_tasks:
                                response_limit_tasks[event.response_id] = asyncio.create_task(
                                    limit_response(event.response_id)
                                )
                        if session.interaction_mode is InteractionMode.GUIDED:
                            lifecycle.guided_phase = "patient_speaking"
                        await send("patient.speech_started", response_id=event.response_id)
                    elif event.type == "patient_transcript_delta":
                        if event.response_id:
                            response_text[event.response_id] = response_text.get(
                                event.response_id, ""
                            ) + (event.text or "")
                            if (
                                spoken_word_count(response_text[event.response_id]) >= 45
                                and event.response_id not in length_limit_attempted
                            ):
                                await interrupt_for_limit(event.response_id)
                        await send(
                            "patient.transcript_delta",
                            text=event.text or "",
                            response_id=event.response_id,
                        )
                    elif event.type == "patient_transcript_final":
                        await send(
                            "patient.response_text",
                            text=event.text or "",
                            response_id=event.response_id,
                        )
                    elif event.type == "patient_interrupted":
                        await send("patient.interrupted", response_id=event.response_id)
                    elif event.type == "error":
                        if event.execution is not None:
                            services.repository.record_execution(event.execution)
                        await send(
                            "voice.error",
                            message="Realtime provider error",
                            detail=event.raw or {},
                        )
                    completed_items = (() if completed is None else (completed,)) + (
                        assembler.drain_ready()
                    )
                    for completed_item in completed_items:
                        await persist_completed(completed_item)
                    if event.type in {"response_completed", "error"}:
                        provider_response_terminal.set()
                        pending_response_interruption = False
                        if event.response_id:
                            limit_task = response_limit_tasks.pop(event.response_id, None)
                            if limit_task is not None:
                                limit_task.cancel()
                        active_response_id = None
                        pending_turns = max(0, pending_turns - 1)
                        if pending_turns == 0:
                            response_idle.set()
                            if session.interaction_mode is InteractionMode.GUIDED:
                                lifecycle.guided_phase = "ready"
                                await send("user.turn.ready")
            except Exception as exc:
                with suppress(Exception):
                    await send("voice.error", message=str(exc))
                    await websocket.close(code=1011)

        async def release_timed_facts() -> None:
            await asyncio.sleep(300)
            if timed_facts_released.is_set():
                return
            ready = realtime_ready.setdefault(session_id, asyncio.Event())
            await ready.wait()
            call = realtime_calls.get(session_id)
            if call is not None:
                # Kept as a lifecycle task for compatibility; no clinical
                # state is sent to the Realtime provider anymore.
                timed_facts_released.set()

        async def request_patient_response(
            call: RealtimeCall, reserved_turn: ConversationTurn | None = None
        ) -> None:
            nonlocal pending_turns
            async with response_create_lock:
                if lifecycle.end_requested.is_set() or finalized.is_set():
                    return
                await provider_response_terminal.wait()
                if lifecycle.end_requested.is_set() or finalized.is_set():
                    return
                provider_response_terminal.clear()
                try:
                    canonical_text: str | None = None
                    if reserved_turn is not None:
                        current_turn = next(
                            (
                                candidate
                                for candidate in services.repository.get_session(session_id).turns
                                if candidate.id == reserved_turn.id
                            ),
                            None,
                        )
                        if current_turn is None:
                            raise InvalidStateError("Reserved practice turn was not found")
                        if not current_turn.patient_text:
                            await services.practice_lifecycle.complete_reserved_turn(
                                session_id, current_turn.id
                            )
                            current_turn = next(
                                candidate
                                for candidate in services.repository.get_session(session_id).turns
                                if candidate.id == reserved_turn.id
                            )
                            canonical_text = current_turn.patient_text
                        else:
                            canonical_text = current_turn.patient_text
                    if not canonical_text:
                        raise InvalidStateError(
                            "A canonical response is required before Realtime synthesis"
                        )
                    await call.create_response(exact_text=canonical_text)
                except ProviderError as exc:
                    services.repository.record_execution(cast(ExecutionRecord, exc.execution))
                    provider_response_terminal.set()
                    pending_turns = max(0, pending_turns - 1)
                    if pending_turns == 0:
                        response_idle.set()
                        lifecycle.guided_phase = "ready"
                        await send("user.turn.ready")
                    await send("response.create_failed", message=str(exc))
                except Exception as exc:
                    services.repository.record_execution(
                        command_failure(call, "realtime_voice.response_create", exc)
                    )
                    provider_response_terminal.set()
                    pending_turns = max(0, pending_turns - 1)
                    if pending_turns == 0:
                        response_idle.set()
                        lifecycle.guided_phase = "ready"
                        await send("user.turn.ready")
                    await send("response.create_failed", message=str(exc))

        await send(
            "call.started",
            session_id=session_id,
            provider_mode=services.settings.provider_mode,
            transport="webrtc",
            voice_stack_id=selected_stack.id,
            voice_stack_version=selected_stack.version,
            models=dict(selected_stack.models),
        )
        pump = asyncio.create_task(pump_events())
        disclosure_timer = asyncio.create_task(release_timed_facts())

        async def start_manual_turn() -> None:
            nonlocal pending_response_interruption, pending_turns
            if session.interaction_mode is not InteractionMode.GUIDED:
                return
            async with lifecycle.manual_turn_lock:
                if lifecycle.manual_recording or lifecycle.end_requested.is_set():
                    return
                if not user_turn_ready.is_set():
                    return
                if lifecycle.guided_phase not in {"ready", "patient_speaking"}:
                    return
                call = realtime_calls.get(session_id)
                if call is None:
                    return
                if pending_turns > 0:
                    if active_response_id is not None:
                        assembler.handle(
                            RealtimeProviderEvent(
                                "patient_interrupted",
                                response_id=active_response_id,
                            )
                        )
                    else:
                        pending_response_interruption = True
                    await call.interrupt_response()
                    await send("patient.interrupted", response_id=active_response_id)
                await call.begin_user_turn()
                realtime_wall_timestamps.setdefault("anonymous", {})["speech_started_at"] = (
                    utc_timestamp()
                )
                lifecycle.manual_recording = True
                lifecycle.guided_phase = "recording"
                pending_turns += 1
                response_idle.clear()
                await send("user.turn.recording")

        async def finish_manual_turn() -> None:
            nonlocal last_speech_stopped_at
            if session.interaction_mode is not InteractionMode.GUIDED:
                return
            async with lifecycle.manual_turn_lock:
                if not lifecycle.manual_recording:
                    return
                lifecycle.manual_recording = False
                lifecycle.guided_phase = "processing"
                await asyncio.sleep(0.2)
                call = realtime_calls.get(session_id)
                if call is None:
                    return
                await call.commit_user_turn()
                last_speech_stopped_at = time.perf_counter()
                realtime_wall_timestamps.setdefault("anonymous", {})["speech_ended_at"] = (
                    utc_timestamp()
                )
                await send("user.turn.committed")

        async def drain_voice() -> None:
            if finalized.is_set():
                await lifecycle.drained.wait()
                return
            finalized.set()
            try:
                provider_response_terminal.set()
                queued_response_tasks = tuple(response_create_tasks)
                for task in queued_response_tasks:
                    task.cancel()
                if queued_response_tasks:
                    with suppress(TimeoutError):
                        async with asyncio.timeout(1):
                            await asyncio.gather(
                                *queued_response_tasks,
                                return_exceptions=True,
                            )
                for task in tuple(response_limit_tasks.values()):
                    task.cancel()
                if lifecycle.manual_recording:
                    with suppress(Exception):
                        await finish_manual_turn()
                with suppress(TimeoutError):
                    async with asyncio.timeout(3):
                        await response_idle.wait()
                async with lifecycle.provider_start_lock:
                    call = realtime_calls.get(session_id)
                    if call is not None:
                        with suppress(Exception):
                            await call.close()
                if not pump.done():
                    with suppress(TimeoutError):
                        async with asyncio.timeout(2):
                            await pump
                if not pump.done():
                    pump.cancel()
                    with suppress(asyncio.CancelledError):
                        await pump
                for completed_item in assembler.drain_ready(force_incomplete=True):
                    await persist_completed(completed_item)
                async with processing_lock:
                    pass
            finally:
                services.repository.mark_session_delivery_unconfirmed(session_id)
                sync_metric_delivery_status(session_id)
                stored_opening = services.repository.get_session(session_id).patient_opening
                if (
                    stored_opening is not None
                    and stored_opening.status is PatientOpeningStatus.PENDING
                ):
                    services.repository.save_patient_opening(
                        replace(stored_opening, status=PatientOpeningStatus.FAILED)
                    )
                lifecycle.drained.set()

        async def end_on_http_request() -> None:
            await lifecycle.end_requested.wait()
            await drain_voice()
            await send("call.ended", session_id=session_id)
            with suppress(Exception):
                await websocket.close(code=1000)

        end_watcher = asyncio.create_task(end_on_http_request())

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    control = ClientControlMessage.model_validate_json(raw)
                except ValidationError as exc:
                    await send("voice.error", message="Invalid control event", detail=str(exc))
                    continue
                if control.type == "call.end":
                    lifecycle.end_requested.set()
                    await drain_voice()
                    await send("call.ended", session_id=session_id)
                    break
                if control.type in {"audio.playback_started", "audio.playback_completed"}:
                    await acknowledge_realtime_audio(control)
                    continue
                if control.type == "user.turn.start":
                    await start_manual_turn()
                elif control.type == "user.turn.finish":
                    await finish_manual_turn()
        except WebSocketDisconnect:
            pass
        finally:
            end_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await end_watcher
            await drain_voice()
            disclosure_timer.cancel()
            with suppress(asyncio.CancelledError):
                await disclosure_timer
            async with realtime_lock:
                call = realtime_calls.pop(session_id, None)
                realtime_ready.pop(session_id, None)
            if call is not None:
                with suppress(Exception):
                    await call.close()

    @app.websocket("/ws/sessions/{session_id}/voice")
    async def voice_socket(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        try:
            selected_session = services.repository.get_session(session_id)
            selected_stack = voice_stack_for(selected_session)
        except AriError as exc:
            await websocket.send_json(
                {
                    "type": "voice.error",
                    "data": {
                        "message": "The persisted voice stack is unavailable",
                        "detail": str(exc),
                    },
                }
            )
            await websocket.close(code=4403)
            return
        realtime_engine = services.realtime_voice_for(selected_stack.id)
        async with voice_session_lock:
            rejected = session_id in voice_lifecycles
            lifecycle = VoiceLifecycle()
            if not rejected:
                voice_lifecycles[session_id] = lifecycle
        if rejected:
            await websocket.send_json(
                {
                    "type": "voice.error",
                    "data": {"message": "A voice connection is already active for this session"},
                }
            )
            await websocket.close(code=4409)
            return
        if selected_stack.transport is VoiceTransport.REALTIME:
            if realtime_engine is None:
                await websocket.send_json(
                    {
                        "type": "voice.error",
                        "data": {"message": "The persisted Realtime voice stack is unavailable"},
                    }
                )
                await websocket.close(code=4403)
                lifecycle.drained.set()
                async with voice_session_lock:
                    if voice_lifecycles.get(session_id) is lifecycle:
                        voice_lifecycles.pop(session_id, None)
                return
            try:
                await handle_realtime_socket(websocket, session_id, lifecycle, selected_stack)
            except Exception as exc:
                with suppress(Exception):
                    await websocket.send_json(
                        {"type": "voice.error", "data": {"message": str(exc)}}
                    )
                    await websocket.close(code=1011)
            finally:
                lifecycle.drained.set()
                async with voice_session_lock:
                    if voice_lifecycles.get(session_id) is lifecycle:
                        voice_lifecycles.pop(session_id, None)
            return
        send_lock = asyncio.Lock()
        try:
            services.practice_lifecycle.resume_practice(
                services.practice_lifecycle.voice_handle(session_id)
            )
            session = services.repository.get_session(session_id)
            case = services.orchestrator._case_for_session(session)
            context = ExecutionContext(
                session_id=session.id,
                learner_id=session.learner_id,
                operation="speech_to_text",
                case_version=case.version,
                case_hash=case.content_hash,
            )
            voice_engine = services.pipeline_voice_for(selected_stack.id)
            stt = await voice_engine.open_transcription(context, TranscriptionConfig.for_case(case))
        except Exception as exc:
            if isinstance(exc, ProviderError):
                services.repository.record_execution(cast(ExecutionRecord, exc.execution))
            with suppress(Exception):
                await websocket.send_json({"type": "voice.error", "data": {"message": str(exc)}})
                await websocket.close(code=1011)
            lifecycle.drained.set()
            async with voice_session_lock:
                if voice_lifecycles.get(session_id) is lifecycle:
                    voice_lifecycles.pop(session_id, None)
            return

        async def send(event_type: str, **data: object) -> bool:
            try:
                async with send_lock:
                    await websocket.send_json({"type": event_type, "data": data})
            except (RuntimeError, WebSocketDisconnect):
                return False
            return True

        processing_lock = asyncio.Lock()
        processed_item_ids: set[str] = set()
        pipeline_wall_timestamps: dict[str, dict[str, str]] = {}
        voice_failed = asyncio.Event()

        async def stream_pipeline_audio(
            turn: ConversationTurn,
            *,
            turn_started: float,
            stt_execution: ExecutionRecord | None,
            patient_execution: ExecutionRecord | None,
            stage_wall_timestamps: dict[str, str] | None = None,
            llm_completed_at: float | None = None,
        ) -> ConversationTurn:
            stage_wall_timestamps = dict(stage_wall_timestamps or {})
            audio_stream_id = new_id()
            turn = services.repository.begin_audio_stream(session_id, turn.id, audio_stream_id)
            await send(
                "patient.audio_streaming",
                turn_id=turn.id,
                response_id=turn.provider_response_id,
                audio_stream_id=audio_stream_id,
                attempt=turn.audio_attempt,
            )
            context = ExecutionContext(
                session_id=session.id,
                learner_id=session.learner_id,
                operation="text_to_speech",
                case_version=case.version,
                case_hash=case.content_hash,
                turn_id=turn.id,
            )
            index = 0
            mime_type = "audio/pcm;rate=24000"
            first_audio_ms: int | None = None
            tts_execution: ExecutionRecord | None = None
            all_sent = True
            pcm_remainder = b""
            first_audio_ready_at: float | None = None
            first_audio_sent_at: float | None = None
            stage_wall_timestamps["tts_started_at"] = utc_timestamp()

            def persist_metric(
                current_turn: ConversationTurn,
                *,
                failed: bool,
                error_increment: int = 0,
            ) -> None:
                endpoint_value = (
                    stt_execution.usage.get("speech_end_to_transcript_final_ms")
                    if stt_execution is not None
                    else None
                )
                endpoint_ms = (
                    int(endpoint_value)
                    if isinstance(endpoint_value, int | float) and endpoint_value >= 0
                    else None
                )
                now = time.perf_counter()
                durations: dict[str, int | None] = {
                    "speech_end_to_transcript_final_ms": endpoint_ms,
                    "llm_total_ms": (
                        patient_execution.latency_ms if patient_execution is not None else None
                    ),
                    "llm_complete_to_tts_first_byte_ms": (
                        int((first_audio_ready_at - llm_completed_at) * 1000)
                        if first_audio_ready_at is not None and llm_completed_at is not None
                        else None
                    ),
                    "tts_total_ms": (
                        tts_execution.latency_ms if tts_execution is not None else None
                    ),
                    "speech_end_to_first_audio_sent_ms": (
                        endpoint_ms + int((first_audio_sent_at - turn_started) * 1000)
                        if endpoint_ms is not None and first_audio_sent_at is not None
                        else None
                    ),
                    "turn_total_ms": (
                        endpoint_ms + int((now - turn_started) * 1000)
                        if endpoint_ms is not None
                        else None
                    ),
                }
                available = {key: value for key, value in durations.items() if value is not None}
                domains = {key: ClockDomain.SERVER for key in available}
                existing = services.repository.get_voice_turn_metric(current_turn.id)
                if existing is None:
                    metric = new_voice_metric(
                        services.repository.get_session(session_id),
                        current_turn,
                        transport=VoiceMetricTransport.PIPELINE,
                        application_version=services.settings.application_version,
                        durations=durations,
                        clock_domains=domains,
                        wall_timestamps_utc=stage_wall_timestamps,
                        executions=tuple(
                            item
                            for item in (stt_execution, patient_execution, tts_execution)
                            if item is not None
                        ),
                        status=(
                            VoiceTurnMetricStatus.FAILED
                            if failed
                            else VoiceTurnMetricStatus.COMPLETED
                        ),
                        error_count=error_increment,
                    )
                else:
                    metric = merge_voice_metric(
                        existing,
                        delivery_status=current_turn.delivery_status,
                        durations=available,
                        clock_domains=domains,
                        wall_timestamps_utc=stage_wall_timestamps,
                        error_count_increment=error_increment,
                        retry_count=max(0, current_turn.audio_attempt - 1),
                        status=(
                            VoiceTurnMetricStatus.FAILED
                            if failed
                            else VoiceTurnMetricStatus.COMPLETED
                        ),
                    )
                services.repository.record_voice_turn_metric(metric)

            try:
                async for audio_event in voice_engine.stream_response(turn.patient_text, context):
                    mime_type = audio_event.mime_type
                    if audio_event.type == "chunk" and audio_event.data:
                        audio_bytes = pcm_remainder + audio_event.data
                        complete_length = len(audio_bytes) - (len(audio_bytes) % 2)
                        pcm_remainder = audio_bytes[complete_length:]
                        if complete_length == 0:
                            continue
                        audio_bytes = audio_bytes[:complete_length]
                        if first_audio_ready_at is None:
                            first_audio_ready_at = time.perf_counter()
                            stage_wall_timestamps["tts_first_byte_at"] = utc_timestamp()
                        chunk_sent = await send(
                            "patient.audio_chunk",
                            turn_id=turn.id,
                            response_id=turn.provider_response_id,
                            audio_stream_id=audio_stream_id,
                            audio=base64.b64encode(audio_bytes).decode("ascii"),
                            mime_type=mime_type,
                            index=index,
                        )
                        if chunk_sent and first_audio_sent_at is None:
                            first_audio_sent_at = time.perf_counter()
                            first_audio_ms = int((first_audio_sent_at - turn_started) * 1000)
                            stage_wall_timestamps["first_audio_chunk_sent_at"] = utc_timestamp()
                        all_sent = chunk_sent and all_sent
                        index += 1
                    elif audio_event.execution is not None:
                        tts_execution = audio_event.execution
            except ProviderError as exc:
                tts_execution = cast(ExecutionRecord, exc.execution)
                services.repository.record_execution(tts_execution)
                failed = services.repository.mark_tts_failed(session_id, turn.id, audio_stream_id)
                stage_wall_timestamps["turn_failed_at"] = utc_timestamp()
                persist_metric(failed, failed=True, error_increment=1)
                await send("turn.tts_failed", turn=public_turn(failed), retryable=True)
                await send(
                    "voice.error",
                    message="La synthèse audio a échoué",
                    detail=str(exc),
                    retryable=True,
                    turn_id=turn.id,
                )
                return failed
            if tts_execution is not None:
                if index == 0 or pcm_remainder:
                    tts_execution = replace(
                        tts_execution,
                        status=ExecutionStatus.FAILED,
                        error_code="invalid_pcm_audio",
                        error_message="TTS returned empty or incomplete PCM frames",
                        retryable=True,
                    )
                services.repository.record_execution(tts_execution)
                stage_wall_timestamps["tts_completed_at"] = utc_timestamp()
            if index == 0 or pcm_remainder:
                failed = services.repository.mark_tts_failed(session_id, turn.id, audio_stream_id)
                stage_wall_timestamps["turn_failed_at"] = utc_timestamp()
                persist_metric(failed, failed=True, error_increment=1)
                await send("turn.tts_failed", turn=public_turn(failed), retryable=True)
                return failed
            if not all_sent:
                services.repository.mark_session_delivery_unconfirmed(session_id)
                unconfirmed = services.repository.get_session(session_id).turns[turn.sequence - 1]
                stage_wall_timestamps["turn_ended_at"] = utc_timestamp()
                persist_metric(unconfirmed, failed=True, error_increment=1)
                return unconfirmed
            turn = services.repository.mark_audio_sent(session_id, turn.id, audio_stream_id, index)
            stage_wall_timestamps["audio_send_completed_at"] = utc_timestamp()
            stage_wall_timestamps["turn_ended_at"] = utc_timestamp()
            persist_metric(turn, failed=False)
            correlation = {
                "turn_id": turn.id,
                "response_id": turn.provider_response_id,
                "audio_stream_id": audio_stream_id,
                "mime_type": mime_type,
                "last_index": index - 1,
            }
            await send("patient.audio_sent", **correlation)
            await send("patient.audio_done", **correlation)
            await send(
                "turn.completed",
                turn=public_turn(turn),
                telemetry={
                    "transport": "pipeline",
                    "stt_final_ms": stt_execution.latency_ms if stt_execution else None,
                    "patient_llm_ms": patient_execution.latency_ms if patient_execution else None,
                    "turn_first_audio_ms": first_audio_ms,
                    "tts_first_audio_ms": tts_execution.usage.get("first_audio_ms")
                    if tts_execution
                    else None,
                    "tts_total_ms": tts_execution.latency_ms if tts_execution else None,
                },
            )
            return turn

        async def process_final(
            text: str,
            stt_execution: ExecutionRecord | None = None,
            item_id: str | None = None,
        ) -> None:
            if item_id is not None:
                if item_id in processed_item_ids:
                    return
                processed_item_ids.add(item_id)
            async with processing_lock:
                turn_id = new_id()
                turn_started = time.perf_counter()
                wall_key = item_id or "anonymous"
                stage_wall_timestamps = {
                    **pipeline_wall_timestamps.pop(wall_key, {}),
                    "transcript_final_at": utc_timestamp(),
                }
                if stt_execution is not None:
                    stt_execution = replace(stt_execution, turn_id=turn_id)
                    services.repository.record_execution(stt_execution)
                turn = services.orchestrator.reserve_transcript(
                    session_id,
                    text,
                    turn_id=turn_id,
                    provider_input_item_id=item_id,
                )
                await send("user.transcript_final", text=text)
                await send("turn.persisted", turn=public_turn(turn))
                stage_wall_timestamps["llm_started_at"] = utc_timestamp()
                try:
                    # All patient turns flow through the common lifecycle; the
                    # pipeline still owns STT/TTS and delivery around it.
                    await services.practice_lifecycle.complete_reserved_turn(session_id, turn.id)
                    outcome_turn = services.repository.get_session(session_id).turns[
                        turn.sequence - 1
                    ]
                    patient_execution = next(
                        (
                            execution
                            for execution in services.repository.get_session(session_id).executions
                            if execution.turn_id == turn.id
                            and execution.operation == "patient_simulation"
                        ),
                        None,
                    )
                except ProviderError as exc:
                    failed = services.repository.get_session(session_id).turns[turn.sequence - 1]
                    failed_execution = cast(ExecutionRecord, exc.execution)
                    endpoint_value = (
                        stt_execution.usage.get("speech_end_to_transcript_final_ms")
                        if stt_execution is not None
                        else None
                    )
                    endpoint_ms = (
                        int(endpoint_value)
                        if isinstance(endpoint_value, int | float) and endpoint_value >= 0
                        else None
                    )
                    stage_wall_timestamps["llm_failed_at"] = utc_timestamp()
                    services.repository.record_voice_turn_metric(
                        new_voice_metric(
                            services.repository.get_session(session_id),
                            failed,
                            transport=VoiceMetricTransport.PIPELINE,
                            application_version=services.settings.application_version,
                            durations={
                                "speech_end_to_transcript_final_ms": endpoint_ms,
                                "llm_total_ms": failed_execution.latency_ms,
                            },
                            clock_domains={
                                key: ClockDomain.SERVER
                                for key, value in {
                                    "speech_end_to_transcript_final_ms": endpoint_ms,
                                    "llm_total_ms": failed_execution.latency_ms,
                                }.items()
                                if value is not None
                            },
                            wall_timestamps_utc=stage_wall_timestamps,
                            executions=tuple(
                                item
                                for item in (stt_execution, failed_execution)
                                if item is not None
                            ),
                            status=VoiceTurnMetricStatus.FAILED,
                            error_count=1,
                        )
                    )
                    await send("turn.response_failed", turn=public_turn(failed))
                    await send(
                        "voice.error",
                        message="La réponse du patient a échoué",
                        detail=str(exc),
                        retryable=True,
                        turn_id=turn.id,
                    )
                    return
                llm_completed_at = time.perf_counter()
                stage_wall_timestamps["llm_completed_at"] = utc_timestamp()
                await send(
                    "patient.response_selected",
                    text=outcome_turn.patient_text,
                    turn_id=outcome_turn.id,
                    response_id=outcome_turn.provider_response_id,
                )
                await send(
                    "patient.response_text", text=outcome_turn.patient_text, turn_id=outcome_turn.id
                )
                await stream_pipeline_audio(
                    outcome_turn,
                    turn_started=turn_started,
                    stt_execution=stt_execution,
                    patient_execution=patient_execution,
                    stage_wall_timestamps=stage_wall_timestamps,
                    llm_completed_at=llm_completed_at,
                )

        async def retry_pipeline_tts(turn_id: str) -> None:
            async with processing_lock:
                turn = next(
                    (
                        item
                        for item in services.repository.get_session(session_id).turns
                        if item.id == turn_id
                    ),
                    None,
                )
                if turn is None or turn.response_state not in {
                    TurnResponseState.TTS_FAILED,
                    TurnResponseState.DELIVERY_UNCONFIRMED,
                }:
                    await send("turn.retry_rejected", turn_id=turn_id)
                    return
                retried = await stream_pipeline_audio(
                    turn,
                    turn_started=time.perf_counter(),
                    stt_execution=None,
                    patient_execution=None,
                )
                await send("turn.retry_completed", turn=public_turn(retried))

        async def acknowledge_pipeline_audio(control: ClientControlMessage) -> None:
            try:
                method = (
                    services.repository.confirm_audio_started
                    if control.type == "audio.playback_started"
                    else services.repository.confirm_audio_delivered
                )
                turn = method(
                    session_id,
                    cast(str, control.turn_id),
                    cast(str, control.audio_stream_id),
                    provider_response_id=control.response_id,
                    last_index=cast(int, control.last_index),
                )
                persist_client_playback_observation(turn, control)
                await send(
                    "turn.audio_started"
                    if control.type == "audio.playback_started"
                    else "turn.delivered",
                    turn=public_turn(turn),
                )
            except AriError as exc:
                await send("audio.ack_rejected", turn_id=control.turn_id, message=str(exc))

        async def pump_stt() -> None:
            try:
                async for event in stt.events():
                    if event.type == "speech_started":
                        pipeline_wall_timestamps.setdefault(event.item_id or "anonymous", {})[
                            "speech_started_at"
                        ] = utc_timestamp()
                        await send("user.speech_started")
                    elif event.type == "speech_stopped":
                        pipeline_wall_timestamps.setdefault(event.item_id or "anonymous", {})[
                            "speech_ended_at"
                        ] = utc_timestamp()
                    elif event.type == "transcript_delta":
                        await send("user.transcript_delta", text=event.text or "")
                    elif event.type == "transcript_final" and event.text:
                        await process_final(event.text, event.execution, event.item_id)
                    elif event.type == "error":
                        if event.execution is not None:
                            services.repository.record_execution(event.execution)
                        await send(
                            "voice.error",
                            message="Speech-to-text provider error",
                            detail=event.raw or {},
                        )
                        with suppress(Exception):
                            await websocket.close(code=1011)
                        return
            except Exception as exc:
                voice_failed.set()
                with suppress(Exception):
                    await send("voice.error", message=str(exc))
                    await websocket.close(code=1011)

        await send(
            "call.started",
            session_id=session_id,
            provider_mode=services.settings.provider_mode,
            transport="pipeline",
            voice_stack_id=selected_stack.id,
            voice_stack_version=selected_stack.version,
            models=dict(selected_stack.models),
        )
        pump = asyncio.create_task(pump_stt())
        finalized = asyncio.Event()

        async def drain_voice() -> None:
            if finalized.is_set():
                await lifecycle.drained.wait()
                return
            finalized.set()
            try:
                async with processing_lock:
                    pass
                with suppress(Exception):
                    await stt.close()
                if not pump.done():
                    with suppress(TimeoutError):
                        async with asyncio.timeout(2):
                            await pump
                if not pump.done():
                    pump.cancel()
                    with suppress(asyncio.CancelledError):
                        await pump
            finally:
                services.repository.mark_session_delivery_unconfirmed(session_id)
                sync_metric_delivery_status(session_id)
                lifecycle.drained.set()

        async def end_on_http_request() -> None:
            await lifecycle.end_requested.wait()
            await drain_voice()
            await send("call.ended", session_id=session_id)
            with suppress(Exception):
                await websocket.close(code=1000)

        end_watcher = asyncio.create_task(end_on_http_request())
        try:
            while True:
                message = await websocket.receive()
                if message.get("bytes") is not None:
                    await stt.send_audio(message["bytes"])
                    continue
                raw_text = message.get("text")
                if raw_text is None:
                    continue
                try:
                    control = ClientControlMessage.model_validate_json(raw_text)
                except ValidationError as exc:
                    await send("voice.error", message="Invalid control event", detail=str(exc))
                    continue
                if control.type == "call.end":
                    lifecycle.end_requested.set()
                    await drain_voice()
                    await send("call.ended", session_id=session_id)
                    break
                if control.type in {"audio.playback_started", "audio.playback_completed"}:
                    await acknowledge_pipeline_audio(control)
                    continue
                if control.type == "turn.retry_tts" and control.turn_id:
                    await retry_pipeline_tts(control.turn_id)
                    continue
                if (
                    control.type == "debug.transcript"
                    and services.settings.provider_mode == "fake"
                    and control.transcript
                ):
                    await send("user.speech_started")
                    try:
                        await process_final(control.transcript)
                    except Exception as exc:
                        voice_failed.set()
                        await send("voice.error", message=str(exc))
                        await websocket.close(code=1011)
                        break
        except WebSocketDisconnect:
            pass
        except RuntimeError as exc:
            if not voice_failed.is_set() and not _is_websocket_disconnect_runtime(exc):
                raise
        finally:
            end_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await end_watcher
            await drain_voice()
            async with voice_session_lock:
                if voice_lifecycles.get(session_id) is lifecycle:
                    voice_lifecycles.pop(session_id, None)

    web_dir = Path(__file__).resolve().parents[4] / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


app = create_app()
