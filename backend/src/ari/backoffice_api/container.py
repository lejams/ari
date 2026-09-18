"""Composition of the back-office: the content pipeline plus accounts and sessions."""

from __future__ import annotations

from dataclasses import dataclass

from ari.application.ports.llm import LLMProvider
from ari.backoffice_api.auth import BackofficeAuth
from ari.config import Settings
from ari.content.container import ContentContainer, build_content_container
from ari.infrastructure.persistence.content.accounts import SqlAccountStore


@dataclass(frozen=True, slots=True)
class Backoffice:
    settings: Settings
    content: ContentContainer
    auth: BackofficeAuth
    accounts: SqlAccountStore


def build_backoffice(settings: Settings, *, llm: LLMProvider | None = None) -> Backoffice:
    content = build_content_container(settings, llm=llm)
    accounts = SqlAccountStore(content.repository.engine)  # type: ignore[attr-defined]
    auth = BackofficeAuth(
        accounts,
        session_days=settings.backoffice_session_days,
        invitation_hours=settings.backoffice_invitation_hours,
    )
    return Backoffice(settings=settings, content=content, auth=auth, accounts=accounts)
