from __future__ import annotations

from pathlib import Path

import pytest

from ari.config import PROJECT_ROOT, Settings
from ari.container import Container, build_container


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = Settings(
        environment="test",
        provider_mode="fake",
        enable_english_technical_test=False,
        database_url=f"sqlite:///{tmp_path / 'ari-test.db'}",
        case_directory=PROJECT_ROOT / "cases",
        prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
    )
    result = build_container(settings)
    result.repository.initialize_schema()
    return result


@pytest.fixture
def technical_container(tmp_path: Path) -> Container:
    settings = Settings(
        environment="test",
        provider_mode="fake",
        enable_english_technical_test=True,
        database_url=f"sqlite:///{tmp_path / 'ari-technical-test.db'}",
        case_directory=PROJECT_ROOT / "cases",
        prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
    )
    result = build_container(settings)
    result.repository.initialize_schema()
    return result
