"""Alembic access for the platform database (section `platform` of alembic.ini)."""

from alembic.config import Config

from ari.infrastructure.persistence import schema

INI_SECTION: schema.Database = "platform"


def alembic_config(database_url: str) -> Config:
    return schema.alembic_config(database_url, INI_SECTION)


def upgrade_to_head(database_url: str) -> None:
    schema.upgrade_to_head(database_url, INI_SECTION)
