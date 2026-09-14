from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from contextlib import suppress
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
)
from ari.api.ownership import PROFILE_COOKIE, OwnershipMiddleware
from ari.api.practice import practice_router
from ari.api.public_session import public_session, public_turn
from ari.application.contracts import ExecutionContext, TranscriptionConfig
from ari.application.schemas import ClientControlMessage
from ari.application.services.telemetry import (
    aggregate_voice_metrics,
    merge_voice_metric,
    new_voice_metric,
    utc_timestamp,
)
from ari.application.voice_stacks import VoiceStack
from ari.config import Settings, get_settings
from ari.container import Container, build_container
from ari.domain.errors import AriError, InvalidStateError, NotFoundError, ProviderError
from ari.domain.models import (
    ClockDomain,
    ConversationSession,
    ConversationTurn,
    ExecutionRecord,
    ExecutionStatus,
    LearningGoal,
    LearningMode,
    MedicalCase,
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
    analysis_locks: dict[str, asyncio.Lock] = {}

    def activate_if_created(session_id: str) -> None:
        if services.repository.get_session(session_id).status is SessionStatus.CREATED:
            services.orchestrator.activate(session_id)

    def voice_stack_for(session: ConversationSession) -> VoiceStack:
        return services.voice_stack.resolve_persisted(
            session.voice_stack_id,
            session.voice_stack_version,
            session.voice_stack_config,
        )

    def session_payload(session: ConversationSession) -> dict[str, object]:
        return public_session(session)

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

    app = FastAPI(title="ARI FSP POC", version="0.1.0")
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
            "technical_test_enabled": services.settings.enable_english_technical_test,
        }

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
        session = services.orchestrator.create_session(
            body.learner_id,
            body.case_id,
            body.case_version,
            body.interaction_mode,
            scenario_id=body.scenario_id,
            scenario_version=body.scenario_version,
            learning_mode=body.learning_mode,
            start_request_id=body.request_id,
            start_request_hash=request_hash if body.request_id else None,
        )
        return session_payload(services.repository.get_session(session.id))

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> Any:
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
            await services.orchestrator.end_session(session_id)
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
            await services.orchestrator.end_session(session_id)
            return session_payload(services.repository.get_session(session_id))

    @app.get("/api/learners/{learner_id}/sessions")
    async def list_sessions(learner_id: str) -> Any:
        return [session_payload(item) for item in services.repository.list_sessions(learner_id)]

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
        send_lock = asyncio.Lock()
        try:
            activate_if_created(session_id)
            session = services.repository.get_session(session_id)
            case = services.orchestrator._case_for_session(session)
            context = ExecutionContext(
                session_id=session.id,
                learner_id=session.learner_id,
                operation="speech_to_text",
                case_version=case.version,
                case_hash=case.content_hash,
            )
            voice_engine = services.voice
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
                    await services.orchestrator.complete_turn(turn)
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
