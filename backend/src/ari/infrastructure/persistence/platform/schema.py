"""Alembic access for the platform database; the only place that knows the ini section."""

from alembic import command
from alembic.config import Config

from ari.config import PROJECT_ROOT

INI_SECTION = "platform"


def alembic_config(database_url: str) -> Config:
    config = Config(PROJECT_ROOT / "alembic.ini", ini_section=INI_SECTION)
    config.attributes["database_url"] = database_url
    return config


def upgrade_to_head(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")
