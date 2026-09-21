"""Alembic access for every ARI database; one ini section per database in `alembic.ini`."""

from typing import Literal

from alembic import command
from alembic.config import Config

from ari.config import PROJECT_ROOT

Database = Literal["platform", "content"]


def alembic_config(database_url: str, section: Database) -> Config:
    config = Config(PROJECT_ROOT / "alembic.ini", ini_section=section)
    config.attributes["database_url"] = database_url
    return config


def upgrade_to_head(database_url: str, section: Database) -> None:
    command.upgrade(alembic_config(database_url, section), "head")
