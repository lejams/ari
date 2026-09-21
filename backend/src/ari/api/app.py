from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ari.api.dto import (
    CreateLearnerRequest,
    CreateSessionRequest,
    UpdateGoalRequest,
    UpdateProfileRequest,
)
from ari.api.lexicon import lexicon_router, public_report
from ari.api.ownership import PROFILE_COOKIE, OwnershipMiddleware
from ari.api.placement import placement_router
from ari.api.practice import practice_router
from ari.api.program import program_router
from ari.api.realtime_socket import RealtimeVoiceSocket
from ari.api.voice_session_dto import public_session
from ari.api.voice_socket import VoiceLifecycles, VoiceSocket
from ari.application.services.catalog import land_summary
from ari.config import Settings, get_settings
from ari.container import Container, build_container
from ari.domain.errors import AriError, InvalidStateError, NotFoundError, ProviderError
from ari.domain.geography import Land
from ari.domain.models import (
    CEFRLevel,
    LearnerDetails,
    LearningGoal,
    MedicalCase,
    SessionStatus,
)
from ari.infrastructure.persistence.platform.identity import ProfileCredentials


def _payload(value: object) -> Any:
    return jsonable_encoder(value)


def _public_case(case: MedicalCase) -> dict[str, object]:
    return {
        "id": case.id,
        "version": case.version,
        "validation_status": case.validation_status,
        "title": case.title,
        "language": case.language,
        "public_summary": case.public_summary,
        "difficulty": case.difficulty,
        "cefr": case.cefr,
        "land": case.land.value if case.land else None,
        "city": case.city,
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
    voice_lifecycles = VoiceLifecycles()
    analysis_locks: dict[str, asyncio.Lock] = {}

    app = FastAPI(title="ARI FSP POC", version="0.1.0")
    app.state.container = services
    app.include_router(practice_router(services))
    app.include_router(lexicon_router(services))
    app.include_router(placement_router(services))
    app.include_router(program_router(services))

    def _session_view(session_id: str) -> Any:
        session = services.repository.get_session(session_id)
        result = public_session(session)
        # The lexicon report is only shown once the analysis is complete.
        result["lexicon"] = (
            public_report(services.lexicon.repository.get_report(session_id))
            if session.status is SessionStatus.COMPLETED
            else None
        )
        return result

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
        }

    @app.get("/api/cases")
    async def list_cases() -> list[dict[str, object]]:
        return [_public_case(case) for case in services.cases.list()]

    @app.get("/api/reference/laender")
    async def reference_laender() -> list[str]:
        """The closed list of Länder, the same one the case contract and the profile use."""
        return [land.value for land in Land]

    @app.get("/api/cases/summary")
    async def cases_summary(request: Request) -> dict[str, object]:
        learner_id = request.state.learner_id
        learner = services.repository.get_learner(learner_id)
        worked = {
            session.case_id
            for session in services.repository.list_sessions(learner_id)
            if session.status is SessionStatus.COMPLETED
        }
        return land_summary(services.cases.list(), worked, learner.details.land)

    @app.post("/api/learners", status_code=201)
    async def create_learner(
        body: CreateLearnerRequest,
        request: Request,
        response: Response,
    ) -> Any:
        if request.state.learner_id:
            return _payload(services.repository.get_learner(request.state.learner_id))
        learner = services.orchestrator.create_learner(
            body.target_cefr,
            details=body.details.apply(LearnerDetails()) if body.details else None,
        )
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
        goal = LearningGoal(body.target_exam, CEFRLevel(body.target_cefr), body.rubric_version)
        return _payload(services.orchestrator.update_goal(learner_id, goal).goal)

    @app.patch("/api/learners/{learner_id}/profile")
    async def update_profile(learner_id: str, body: UpdateProfileRequest) -> Any:
        current = services.repository.get_learner(learner_id)
        return _payload(
            services.orchestrator.update_details(learner_id, body.apply(current.details))
        )

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
                return public_session(existing)
        selected = services.cases.get(
            body.case_id,
            body.case_version,
            scenario_id=body.scenario_id,
            scenario_version=body.scenario_version,
        )
        if selected.validation_status != "published":
            raise InvalidStateError("Aucun scénario vocal approuvé disponible pour ce choix")
        session = services.orchestrator.create_session(
            body.learner_id,
            body.case_id,
            body.case_version,
            scenario_id=body.scenario_id,
            scenario_version=body.scenario_version,
            learning_mode=body.learning_mode,
            start_request_id=body.request_id,
            start_request_hash=request_hash if body.request_id else None,
        )
        return public_session(services.repository.get_session(session.id))

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> Any:
        return _session_view(session_id)

    @app.post("/api/sessions/{session_id}/end")
    async def end_session(session_id: str) -> Any:
        session = services.repository.get_session(session_id)
        lifecycle = None
        if session.status is SessionStatus.ACTIVE:
            lifecycle = await voice_lifecycles.request_end(session_id)
        if lifecycle is not None and not lifecycle.drained.is_set():
            try:
                async with asyncio.timeout(65):
                    await lifecycle.drained.wait()
            except TimeoutError as exc:
                raise InvalidStateError("Voice session is still being finalized") from exc
        async with analysis_locks.setdefault(session_id, asyncio.Lock()):
            await services.orchestrator.end_session(session_id)
            return _session_view(session_id)

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
            return _session_view(session_id)

    @app.get("/api/learners/{learner_id}/sessions")
    async def list_sessions(learner_id: str) -> Any:
        return [public_session(item) for item in services.repository.list_sessions(learner_id)]

    @app.websocket("/ws/sessions/{session_id}/voice")
    async def voice_socket(websocket: WebSocket, session_id: str) -> None:
        socket_class: type[VoiceSocket] = VoiceSocket
        with suppress(AriError):
            stack_id = services.repository.get_session(session_id).voice_stack_id
            if stack_id == services.realtime_stack.id:
                socket_class = RealtimeVoiceSocket
        await socket_class(services, voice_lifecycles, websocket, session_id).run()

    web_dir = Path(__file__).resolve().parents[4] / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
