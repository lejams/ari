"""Learner accounts over HTTP: e-mailed links, password, login, logout, who am I.

The session cookie is SameSite=Lax rather than Strict so a learner who follows a link to the
app from an e-mail or another site arrives signed in. Cross-site mutations stay refused: the
ownership middleware checks their origin, and browsers never send Lax cookies on them.

E-mails leave after the response, so how long a request takes never tells whether the
address has an account; a delivery failure is logged, not returned.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ari.api.dto import (
    AccountEmailRequest,
    AccountLinkRequest,
    AccountLoginRequest,
    AccountPasswordRequest,
)
from ari.api.ownership import PROFILE_COOKIE, SESSION_COOKIE
from ari.application.ports.email import EmailMessage
from ari.container import Container
from ari.domain.accounts import LearnerAccount, normalise_email
from ari.domain.errors import EmailDeliveryError, InvalidStateError
from ari.infrastructure.persistence.platform.identity import ProfileCredentials

logger = logging.getLogger(__name__)

# Which address this browser asked a link for (its SHA-256). An alpha profile is only adopted
# when the link is opened where it was requested, never from a link someone else forwarded.
REQUESTED_COOKIE = "ari_link_requested"


def _me(account: LearnerAccount) -> dict[str, Any]:
    return {"email": account.email, "has_learner": account.learner_id is not None}


def _fingerprint(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def auth_router(services: Container, credentials: ProfileCredentials) -> APIRouter:
    router = APIRouter()
    secure = services.settings.environment == "production"
    max_age = services.settings.learner_session_days * 24 * 3600
    link_max_age = services.settings.learner_activation_hours * 3600

    def _deliver(message: EmailMessage) -> None:
        try:
            services.email.send(message)
        except EmailDeliveryError:
            logger.exception("account e-mail not delivered")

    def _link_requested(body: AccountEmailRequest, background: BackgroundTasks) -> Response:
        response = JSONResponse({"status": "sent"}, status_code=202)
        response.set_cookie(
            REQUESTED_COOKIE,
            _fingerprint(normalise_email(body.email)),
            httponly=True,
            samesite="lax",
            secure=secure,
            max_age=link_max_age,
            path="/",
        )
        response.background = background
        return response

    def _adopt_alpha_profile(request: Request, account: LearnerAccount) -> LearnerAccount | None:
        """The anonymous alpha learner this browser still holds joins its first account.
        Returns the updated account, or None when nothing was adopted."""
        legacy = request.cookies.get(PROFILE_COOKIE)
        learner_id = credentials.resolve(legacy)
        if not legacy or not learner_id or account.learner_id is not None:
            return None
        if not services.auth.link_learner(account.id, learner_id):
            return None
        credentials.revoke(legacy)
        logger.info("alpha learner %s adopted by account %s", learner_id, account.id)
        return services.auth.account(account.id) or account

    def _signed_in(request: Request, token: str, *, adopt: bool) -> JSONResponse:
        account = services.auth.resolve(token)
        if account is None:
            raise InvalidStateError("Session indisponible ; reconnectez-vous")
        adopted = _adopt_alpha_profile(request, account) if adopt else None
        response = JSONResponse(_me(adopted or account))
        if adopted:
            # Only now is the old cookie useless; otherwise it stays for a later adoption.
            response.delete_cookie(PROFILE_COOKIE, httponly=True, samesite="strict", secure=secure)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=secure,
            max_age=max_age,
            path="/",
        )
        return response

    @router.post("/api/auth/signup", status_code=202)
    def signup(body: AccountEmailRequest, background: BackgroundTasks) -> Response:
        message = services.auth.signup(body.email)
        if message:
            background.add_task(_deliver, message)
        return _link_requested(body, background)

    @router.post("/api/auth/password/forgot", status_code=202)
    def forgot_password(body: AccountEmailRequest, background: BackgroundTasks) -> Response:
        message = services.auth.request_password_reset(body.email)
        if message:
            background.add_task(_deliver, message)
        return _link_requested(body, background)

    @router.post("/api/auth/links")
    def link(body: AccountLinkRequest) -> dict[str, Any]:
        """What an e-mailed link is for, before the learner types a password. Uses nothing."""
        account = services.auth.link_holder(body.token)
        if account is None:
            raise HTTPException(status_code=404, detail="Lien inconnu, expiré ou déjà utilisé")
        return {"email": account.email, "activation": not account.verified}

    @router.post("/api/auth/password")
    def set_password(body: AccountPasswordRequest, request: Request) -> JSONResponse:
        holder = services.auth.link_holder(body.token)
        requested_here = holder is not None and request.cookies.get(
            REQUESTED_COOKIE
        ) == _fingerprint(holder.email)
        token = services.auth.set_password(body.token, body.password)
        response = _signed_in(request, token, adopt=requested_here)
        response.delete_cookie(REQUESTED_COOKIE, httponly=True, samesite="lax", secure=secure)
        return response

    @router.post("/api/auth/login")
    def login(body: AccountLoginRequest, request: Request) -> JSONResponse:
        # Typing the account's password in this browser is consent enough to adopt.
        return _signed_in(request, services.auth.login(body.email, body.password), adopt=True)

    @router.post("/api/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        services.auth.logout(request.cookies.get(SESSION_COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", secure=secure)
        return response

    @router.get("/api/auth/me")
    def me(request: Request) -> dict[str, Any]:
        account: LearnerAccount | None = request.state.account
        if account is None:
            raise HTTPException(status_code=401, detail="Connexion requise")
        return _me(account)

    return router
