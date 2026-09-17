"""Shared HTTP/WebSocket ownership boundary for local profiles."""

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ari.infrastructure.persistence.identity import ProfileCredentials
from ari.infrastructure.persistence.sqlite import SessionRow

PROFILE_COOKIE = "ari_profile"


class OwnershipMiddleware:
    def __init__(self, app: ASGIApp, *, credentials: ProfileCredentials, origin: str) -> None:
        self.app, self.credentials, self.origin = app, credentials, origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        path = connection.url.path
        protected = path.startswith(
            (
                "/api/learners/",
                "/api/sessions",
                "/ws/sessions/",
                "/api/practice/",
                "/api/lexicon",
                "/api/placement",
            )
        ) or path in (
            "/api/profile",
            "/api/progression",
            "/api/history",
            "/api/program",
            "/api/technical/voice-metrics",
        )
        origin = connection.headers.get("origin")
        same_origin = f"{connection.url.scheme.replace('ws', 'http')}://{connection.url.netloc}"
        mutation = scope["type"] == "websocket" or scope.get("method") not in (
            "GET",
            "HEAD",
            "OPTIONS",
        )
        if mutation and origin and origin not in (same_origin, self.origin):
            await self._refuse(scope, receive, send)
            return
        learner_id = self.credentials.resolve(connection.cookies.get(PROFILE_COOKIE))
        scope.setdefault("state", {})["learner_id"] = learner_id
        if protected and scope.get("method") != "OPTIONS":
            permitted = learner_id is not None
            parts = path.strip("/").split("/")
            if path.startswith("/api/learners/"):
                permitted = permitted and len(parts) > 2 and parts[2] == learner_id
            if path.startswith(("/api/sessions/", "/ws/sessions/")):
                with Session(self.credentials.engine) as db:
                    owner = db.scalar(
                        select(SessionRow.learner_id).where(
                            SessionRow.id == (parts[2] if len(parts) > 2 else ""),
                        )
                    )
                permitted = permitted and owner == learner_id
            if not permitted:
                await self._refuse(scope, receive, send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse({"detail": "Not found or profile unavailable"}, status_code=404)(
                scope,
                receive,
                send,
            )
