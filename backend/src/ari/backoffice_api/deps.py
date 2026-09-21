"""FastAPI dependencies: the calling account and role guards."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request

from ari.content.domain.accounts import AccountContext, Role
from ari.content.domain.documents import Actor


def current_account(request: Request) -> AccountContext:
    account = getattr(request.state, "account", None)
    if account is None:
        raise HTTPException(status_code=401, detail="Connexion requise")
    return account  # type: ignore[no-any-return]


def require_roles(*roles: Role) -> Callable[[Request], AccountContext]:
    def guard(request: Request) -> AccountContext:
        account = current_account(request)
        if not account.has(*roles):
            raise HTTPException(status_code=403, detail="Rôle insuffisant")
        return account

    return guard


def actor(account: AccountContext) -> Actor:
    return Actor(kind="account", name=account.display_name, account_id=account.id)
