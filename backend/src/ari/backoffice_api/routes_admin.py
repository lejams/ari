"""Owner administration: accounts and invitations; the dashboard everyone sees."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder

from ari.backoffice_api.container import Backoffice
from ari.backoffice_api.deps import current_account, require_roles
from ari.backoffice_api.dto import CreateAccountRequest
from ari.content.domain.accounts import Account, AccountContext, Role
from ari.content.domain.protocol import ProtocolStatus

Owner = Annotated[AccountContext, Depends(require_roles(Role.OWNER))]
Anyone = Annotated[AccountContext, Depends(current_account)]


def _account(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "email": account.email,
        "display_name": account.display_name,
        "roles": sorted(role.value for role in account.roles),
        "active": account.active,
        "has_password": account.has_password,
        "created_at": account.created_at.isoformat(),
    }


def admin_router(services: Backoffice) -> APIRouter:
    router = APIRouter()
    content = services.content

    def _invitation_url(token: str) -> str:
        return f"{services.settings.backoffice_origin}/#/invitation/{token}"

    @router.post("/api/accounts", status_code=201)
    def create_account(body: CreateAccountRequest, account: Owner) -> dict[str, Any]:
        invitation = services.auth.create_account(
            body.email, body.display_name, body.roles, actor=account.id
        )
        return {
            "account": _account(invitation.account),
            "invitation_url": _invitation_url(invitation.token),
            "expires_at": invitation.expires_at.isoformat(),
        }

    @router.get("/api/accounts")
    def list_accounts(account: Owner) -> dict[str, Any]:
        items = [_account(a) for a in services.auth.list_accounts()]
        return {"items": items, "total": len(items)}

    @router.post("/api/accounts/{account_id}/deactivate")
    def deactivate(account_id: str, account: Owner) -> dict[str, Any]:
        return _account(services.auth.deactivate(account_id, actor=account.id))

    @router.post("/api/accounts/{account_id}/invitations", status_code=201)
    def invite(account_id: str, account: Owner) -> dict[str, Any]:
        invitation = services.auth.invite(account_id, actor=account.id)
        return {
            "account": _account(invitation.account),
            "invitation_url": _invitation_url(invitation.token),
            "expires_at": invitation.expires_at.isoformat(),
        }

    @router.get("/api/dashboard")
    def dashboard(account: Anyone) -> dict[str, Any]:
        jobs = Counter(job.status.value for job in content.queue.list())
        with content.repository.transaction() as tx:
            documents = Counter(d.status.value for d in tx.list_documents())
            heads = tx.list_heads()
            gold = tx.list_gold()
        protocols = Counter(h.status.value for h in heads)
        return {
            "jobs_by_status": dict(jobs),
            "documents_by_status": dict(documents),
            "protocols_by_status": dict(protocols),
            "review_queue": protocols.get(ProtocolStatus.DOCTOR_REVIEW.value, 0)
            + protocols.get(ProtocolStatus.CHANGES_REQUESTED.value, 0),
            "owner_queue": protocols.get(ProtocolStatus.DOCTOR_APPROVED.value, 0),
            "gold_by_land": dict(
                Counter(g.location.land.value for g in gold if g.location.land is not None)
            ),
            "gold_total": len(gold),
            "me": jsonable_encoder(account),
        }

    return router
