"""Shared HTTP/WebSocket ownership boundary: account session → the account's learner."""

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ari.application.services.accounts import LearnerAuth
from ari.infrastructure.persistence.platform.repository import SessionRow

SESSION_COOKIE = "ari_session"
# Anonymous alpha profile. No longer a credential: only read once, to adopt its learner.
PROFILE_COOKIE = "ari_profile"


class OwnershipMiddleware:
    def __init__(self, app: ASGIApp, *, auth: LearnerAuth, engine: Engine, origin: str) -> None:
        self.app, self.auth, self.engine, self.origin = app, auth, engine, origin

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
            "/api/cases/summary",
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
        account = self.auth.resolve(connection.cookies.get(SESSION_COOKIE))
        learner_id = account.learner_id if account else None
        state = scope.setdefault("state", {})
        state["account"], state["learner_id"] = account, learner_id
        if protected and scope.get("method") != "OPTIONS":
            permitted = learner_id is not None
            parts = path.strip("/").split("/")
            if path.startswith("/api/learners/"):
                permitted = permitted and len(parts) > 2 and parts[2] == learner_id
            if path.startswith(("/api/sessions/", "/ws/sessions/")):
                with Session(self.engine) as db:
                    owner = db.scalar(
                        select(SessionRow.learner_id).where(
                            SessionRow.id == (parts[2] if len(parts) > 2 else ""),
                        )
                    )
                permitted = permitted and owner == learner_id
            if not permitted:
                await self._refuse(scope, receive, send, signed_in=account is not None)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(
        scope: Scope, receive: Receive, send: Send, *, signed_in: bool = True
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        elif not signed_in:
            # No session at all: the pages send the visitor back to sign in.
            await JSONResponse({"detail": "Connexion requise"}, status_code=401)(
                scope, receive, send
            )
        else:
            await JSONResponse({"detail": "Not found or profile unavailable"}, status_code=404)(
                scope,
                receive,
                send,
            )
