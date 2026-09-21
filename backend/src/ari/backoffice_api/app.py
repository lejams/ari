"""The back-office FastAPI application (port 8100): API plus the static `backoffice-web/` pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ari.backoffice_api.container import Backoffice, build_backoffice
from ari.backoffice_api.middleware import BackofficeAuthMiddleware
from ari.backoffice_api.routes_admin import admin_router
from ari.backoffice_api.routes_auth import auth_router
from ari.backoffice_api.routes_bundles import bundles_router
from ari.backoffice_api.routes_documents import documents_router
from ari.backoffice_api.routes_protocols import protocols_router
from ari.config import Settings, get_settings
from ari.content.domain.errors import ConflictError
from ari.domain.errors import AriError, NotFoundError, ProviderError


def create_backoffice_app(
    services: Backoffice | None = None, settings: Settings | None = None
) -> FastAPI:
    services = services or build_backoffice(settings or get_settings())
    app = FastAPI(title="ARI back-office", version="0.1.0")
    app.state.services = services
    app.include_router(auth_router(services))
    app.include_router(documents_router(services))
    app.include_router(protocols_router(services))
    app.include_router(bundles_router(services))
    app.include_router(admin_router(services))
    app.add_middleware(
        BackofficeAuthMiddleware, auth=services.auth, origin=services.settings.backoffice_origin
    )

    @app.exception_handler(AriError)
    async def ari_error(_: Request, exc: AriError) -> JSONResponse:
        if isinstance(exc, ProviderError):
            status = 502
        elif isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ConflictError):
            status = 409
        else:
            status = 400
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "provider_mode": services.settings.provider_mode}

    web_dir = Path(__file__).resolve().parents[4] / "backoffice-web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="backoffice-web")
    return app
