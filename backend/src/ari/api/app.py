from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ari.api.auth import auth_router
from ari.api.dto import (
    CreateLearnerRequest,
    CreateSessionRequest,
    UpdateGoalRequest,
    UpdateProfileRequest,
)
from ari.api.lexicon import lexicon_router, public_report
from ari.api.ownership import OwnershipMiddleware
from ari.api.pause import ServicePauseMiddleware
from ari.api.placement import placement_router
from ari.api.practice import practice_router
from ari.api.program import program_router
from ari.api.realtime_socket import RealtimeVoiceSocket
from ari.api.voice_session_dto import public_session
from ari.api.voice_socket import VoiceLifecycles, VoiceSocket
from ari.application.services.catalog import land_summary
from ari.config import Settings, get_settings
from ari.container import Container, build_container
from ari.domain.errors import (
    AriError,
    EmailDeliveryError,
    InvalidStateError,
    NotFoundError,
    ProviderError,
)
from ari.domain.geography import Land
from ari.domain.models import (
    CEFRLevel,
    LearnerDetails,
    LearningGoal,
    MedicalCase,
    SessionStatus,
)
from ari.infrastructure.persistence.platform.identity import ProfileCredentials

# Pages and modules change under the same URL with each deploy (the app itself moved from "/"
# to "/app"). Without it browsers guess a freshness from Last-Modified and keep showing an old
# page for hours; with it they revalidate every time, a cheap 304 when nothing changed.
REVALIDATE = {"Cache-Control": "no-cache"}


class RevalidatedStaticFiles(StaticFiles):
    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.update(REVALIDATE)
        return response


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
    app.include_router(auth_router(services, credentials))
    # Starlette runs the last-added middleware outermost. Adding the pause guard before
    # ownership makes it run innermost: strangers still get a 404 from ownership, only
    # authenticated learners reach the pause guard and see its message.
    app.add_middleware(ServicePauseMiddleware, paused=services.settings.service_paused)
    app.add_middleware(
        OwnershipMiddleware,
        auth=services.auth,
        engine=services.repository.engine,
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
        if isinstance(exc, ProviderError | EmailDeliveryError):
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
    async def create_learner(body: CreateLearnerRequest, request: Request) -> Any:
        """The onboarding: the signed-in account gets its one learner."""
        account = request.state.account
        if account is None:
            raise HTTPException(status_code=401, detail="Connexion requise")
        if request.state.learner_id:
            return _payload(services.repository.get_learner(request.state.learner_id))
        learner = services.orchestrator.create_learner(
            body.target_cefr,
            details=body.details.apply(LearnerDetails()) if body.details else None,
        )
        if not services.auth.link_learner(account.id, learner.id):
            # A concurrent onboarding linked its learner first: that one is the learner.
            linked = services.auth.account(account.id)
            if linked is None or linked.learner_id is None:
                raise InvalidStateError("Profil indisponible ; réessayez")
            return _payload(services.repository.get_learner(linked.learner_id))
        return _payload(learner)

    @app.get("/api/profile")
    async def current_profile(request: Request) -> Any:
        return _payload(services.repository.get_learner(request.state.learner_id))

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
        # The public landing owns "/", sign-in lives at "/connexion", the learner app at "/app".
        # All are declared before the static mount, which would otherwise answer "/" with
        # index.html. The app pages send visitors without an account session to sign in.
        def _page(name: str) -> FileResponse:
            return FileResponse(web_dir / name, headers=REVALIDATE)

        def _signed_in_page(request: Request, name: str, *, needs_learner: bool) -> Response:
            account = request.state.account
            if account is None:
                return RedirectResponse("/connexion", status_code=303, headers=REVALIDATE)
            if needs_learner and account.learner_id is None:
                # The onboarding comes first.
                return RedirectResponse("/app", status_code=303, headers=REVALIDATE)
            return _page(name)

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        async def landing_page() -> FileResponse:
            return _page("landing.html")

        @app.api_route("/connexion", methods=["GET", "HEAD"], include_in_schema=False)
        async def sign_in_page() -> FileResponse:
            return _page("login.html")

        @app.api_route("/app", methods=["GET", "HEAD"], include_in_schema=False)
        async def learner_app(request: Request) -> Response:
            return _signed_in_page(request, "index.html", needs_learner=False)

        @app.api_route("/voice.html", methods=["GET", "HEAD"], include_in_schema=False)
        async def voice_page(request: Request) -> Response:
            return _signed_in_page(request, "voice.html", needs_learner=True)

        app.mount("/", RevalidatedStaticFiles(directory=web_dir, html=True), name="web")
    return app
