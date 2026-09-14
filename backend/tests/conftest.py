from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from clinical_fixtures import simulated_review, synthetic_bundle

from ari.config import PROJECT_ROOT, Settings
from ari.container import Container, build_container


def migrated_database_url(path: Path) -> str:
    """Create a temporary database with the same Alembic migration used in production."""
    url = f"sqlite:///{path}"
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


def build_test_container(path: Path) -> Container:
    settings = Settings(
        _env_file=None,
        environment="test",
        provider_mode="fake",
        database_url=migrated_database_url(path),
        prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
    )
    return build_container(settings)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    """Empty registry: nothing is published."""
    return build_test_container(tmp_path / "ari-test.db")


@pytest.fixture
def published_container(tmp_path: Path) -> Container:
    """One synthetic voice scenario imported, reviewed by simulated test reviewers and published."""
    result = build_test_container(tmp_path / "ari-published.db")
    bundle = synthetic_bundle()
    store = result.cases.store
    store.import_bundle(bundle)
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))
    store.publish(bundle.scenarios[0].id, bundle.scenarios[0].version, actor="ISOLATED TEST")
    return result
