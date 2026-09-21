"""Cookie session → account on every request; same-origin check on mutations; 401 otherwise."""

from __future__ import annotations

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ari.backoffice_api.auth import BackofficeAuth

SESSION_COOKIE = "ari_backoffice"
PUBLIC_PATHS = ("/api/health", "/api/auth/login", "/api/auth/invitations/")


class BackofficeAuthMiddleware:
    def __init__(self, app: ASGIApp, *, auth: BackofficeAuth, origin: str) -> None:
        self.app, self.auth, self.origin = app, auth, origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        path = connection.url.path
        method = scope.get("method", "GET")
        same_origin = f"{connection.url.scheme}://{connection.url.netloc}"
        origin = connection.headers.get("origin")
        if method not in ("GET", "HEAD", "OPTIONS") and origin not in (
            None,
            same_origin,
            self.origin,
        ):
            await self._refuse(scope, receive, send, 403, "Origine refusée")
            return
        account = self.auth.resolve(connection.cookies.get(SESSION_COOKIE))
        scope.setdefault("state", {})["account"] = account
        if (
            path.startswith("/api/")
            and not path.startswith(PUBLIC_PATHS)
            and method != "OPTIONS"
            and account is None
        ):
            await self._refuse(scope, receive, send, 401, "Connexion requise")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
        await JSONResponse(status_code=status, content={"detail": detail})(scope, receive, send)
