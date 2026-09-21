"""Composition of the back-office: the content pipeline plus accounts and sessions."""

from __future__ import annotations

from dataclasses import dataclass

from ari.application.ports.llm import LLMProvider
from ari.backoffice_api.auth import BackofficeAuth
from ari.config import Settings
from ari.content.container import ContentContainer, build_content_container
from ari.content.registry_bridge import RegistryBridge
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.persistence.content.accounts import SqlAccountStore
from ari.infrastructure.persistence.platform.engine import create_platform_engine


@dataclass(frozen=True, slots=True)
class Backoffice:
    settings: Settings
    content: ContentContainer
    auth: BackofficeAuth
    accounts: SqlAccountStore
    registry: RegistryBridge  # The platform registry, reached with the platform credentials.


def build_backoffice(settings: Settings, *, llm: LLMProvider | None = None) -> Backoffice:
    content = build_content_container(settings, llm=llm)
    accounts = SqlAccountStore(content.repository.engine)  # type: ignore[attr-defined]
    auth = BackofficeAuth(
        accounts,
        session_days=settings.backoffice_session_days,
        invitation_hours=settings.backoffice_invitation_hours,
    )
    registry = RegistryBridge(
        ClinicalStore(create_platform_engine(settings.database_url)), content.repository
    )
    return Backoffice(
        settings=settings, content=content, auth=auth, accounts=accounts, registry=registry
    )
