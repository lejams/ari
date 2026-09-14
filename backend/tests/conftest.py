from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from ari.config import PROJECT_ROOT, Settings
from ari.container import Container, build_container


def migrated_database_url(path: Path) -> str:
    """Create a temporary database with the same Alembic migration used in production."""
    url = f"sqlite:///{path}"
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = Settings(
        environment="test",
        provider_mode="fake",
        enable_english_technical_test=False,
        database_url=migrated_database_url(tmp_path / "ari-test.db"),
        case_directory=PROJECT_ROOT / "cases",
        prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
    )
    return build_container(settings)


@pytest.fixture
def technical_container(tmp_path: Path) -> Container:
    settings = Settings(
        environment="test",
        provider_mode="fake",
        enable_english_technical_test=True,
        database_url=migrated_database_url(tmp_path / "ari-technical-test.db"),
        case_directory=PROJECT_ROOT / "cases",
        prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
    )
    return build_container(settings)
