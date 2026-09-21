"""Login, logout, who am I, and the one-time invitation flow that sets a password."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ari.backoffice_api.container import Backoffice
from ari.backoffice_api.deps import current_account
from ari.backoffice_api.dto import LoginRequest, SetPasswordRequest
from ari.backoffice_api.middleware import SESSION_COOKIE
from ari.content.domain.accounts import AccountContext


def _me(account: AccountContext) -> dict[str, Any]:
    return {
        "id": account.id,
        "display_name": account.display_name,
        "roles": sorted(role.value for role in account.roles),
    }


def auth_router(services: Backoffice) -> APIRouter:
    router = APIRouter()
    secure = services.settings.environment == "production"
    max_age = services.settings.backoffice_session_days * 24 * 3600

    def _set_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=secure,
            max_age=max_age,
            path="/",
        )

    @router.post("/api/auth/login")
    def login(body: LoginRequest, response: Response) -> dict[str, Any]:
        token = services.auth.login(body.email, body.password)
        account = services.auth.resolve(token)
        assert account is not None
        _set_cookie(response, token)
        return _me(account)

    @router.post("/api/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        services.auth.logout(request.cookies.get(SESSION_COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict", secure=secure)
        return response

    @router.get("/api/auth/me")
    def me(request: Request) -> dict[str, Any]:
        return _me(current_account(request))

    @router.get("/api/auth/invitations/{token}")
    def invitation(token: str) -> dict[str, Any]:
        account = services.auth.invitation_holder(token)
        if account is None:
            raise HTTPException(status_code=404, detail="Invitation inconnue ou expirée")
        return {"email": account.email, "display_name": account.display_name}

    @router.post("/api/auth/invitations/{token}/password")
    def set_password(token: str, body: SetPasswordRequest, response: Response) -> dict[str, Any]:
        account = services.auth.set_password(token, body.password)
        session = services.auth.login(account.email, body.password)
        context = services.auth.resolve(session)
        assert context is not None
        _set_cookie(response, session)
        return _me(context)

    return router
