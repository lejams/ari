"""Emergency kill switch for the learner application.

When ``ARI_SERVICE_PAUSED`` is true this middleware refuses the endpoints that start new,
provider-costing work: session creation, practice runs, placement attempts and voice
connections. Everything read-only keeps working so learners still see their history.
"""

import json

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

PAUSED_MESSAGE = (
    "Le service ARI est momentanément en pause. Vos sessions passées restent consultables ; "
    "réessayez plus tard."
)

# Exact POST paths that begin a new costed activity. Sub-routes (…/end, …/finish, …/answers)
# stay open so a learner can close what they already started.
_PAUSED_POST_PATHS = frozenset(
    {"/api/sessions", "/api/practice/runs", "/api/placement/attempts"}
)


class ServicePauseMiddleware:
    def __init__(self, app: ASGIApp, *, paused: bool) -> None:
        self.app, self.paused = app, paused

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.paused or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if scope["type"] == "websocket" and path.startswith("/ws/sessions/"):
            await self._refuse_websocket(receive, send)
            return
        if (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and path in _PAUSED_POST_PATHS
        ):
            await JSONResponse(
                {"detail": PAUSED_MESSAGE},
                status_code=503,
                headers={"Retry-After": "3600"},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse_websocket(receive: Receive, send: Send) -> None:
        # Accept first so the client receives a voice.error frame it can display, then close
        # with an application code, mirroring VoiceSocket._refuse.
        await receive()  # websocket.connect
        await send({"type": "websocket.accept"})
        await send(
            {
                "type": "websocket.send",
                "text": json.dumps(
                    {
                        "type": "voice.error",
                        "data": {"message": PAUSED_MESSAGE, "code": "service_paused"},
                    }
                ),
            }
        )
        await send({"type": "websocket.close", "code": 4503})
