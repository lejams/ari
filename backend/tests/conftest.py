"""Isolated PostgreSQL databases per test, cloned from templates migrated once per session.

`ARI_TEST_DATABASE_URL` points at a maintenance database with a role allowed to create
databases (the docker compose superuser by default). Without a reachable server the whole
run stops: a skipped suite would let CI pass on nothing. One template per ARI database
(`platform`, `content`), each migrated with its own Alembic chain.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from clinical_fixtures import simulated_review, synthetic_bundle
from practice_fixtures import synthetic_practice
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from ari.config import PROJECT_ROOT, Settings
from ari.container import Container, build_container
from ari.content.container import ContentContainer, build_content_container
from ari.domain.clinical import ClinicalBundle
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.persistence.schema import Database, upgrade_to_head

ADMIN_URL = os.environ.get(
    "ARI_TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
)
TEMPLATES: dict[Database, str] = {"platform": "ari_test_template", "content": "ari_test_content"}
DatabaseFactory = Callable[[], str]


def _admin_engine() -> Engine:
    return create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)


def _url_for(database: str) -> str:
    return make_url(ADMIN_URL).set(database=database).render_as_string(hide_password=False)


def _create_template(section: Database) -> str:
    """Drop leftovers of a crashed run, create the template and migrate it to head."""
    name = TEMPLATES[section]
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            leftovers = connection.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE :pattern"),
                {"pattern": f"{name}%"},
            ).scalars()
            for leftover in list(leftovers):
                connection.execute(text(f'DROP DATABASE IF EXISTS "{leftover}" WITH (FORCE)'))
            connection.execute(text(f"CREATE DATABASE {name}"))
    except OperationalError as exc:
        pytest.exit(f"PostgreSQL de test inaccessible ({ADMIN_URL}) : lancez `make db-up`. {exc}")
    finally:
        engine.dispose()
    upgrade_to_head(_url_for(name), section)
    return name


def _drop(name: str) -> None:
    engine = _admin_engine()
    with engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


def _factory(template: str) -> Iterator[DatabaseFactory]:
    engine = _admin_engine()
    created: list[str] = []

    def make() -> str:
        name = f"{template}_{uuid4().hex[:12]}"
        with engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {name} TEMPLATE {template}"))
        created.append(name)
        return _url_for(name)

    yield make
    with engine.connect() as connection:
        for name in created:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


@pytest.fixture(scope="session")
def template_database() -> Iterator[str]:
    name = _create_template("platform")
    yield name
    _drop(name)


@pytest.fixture(scope="session")
def content_template_database() -> Iterator[str]:
    name = _create_template("content")
    yield name
    _drop(name)


@pytest.fixture
def new_database_url(template_database: str) -> Iterator[DatabaseFactory]:
    """A factory for tests that need more than one isolated platform database."""
    yield from _factory(template_database)


@pytest.fixture
def new_content_database_url(content_template_database: str) -> Iterator[DatabaseFactory]:
    yield from _factory(content_template_database)


@pytest.fixture
def database_url(new_database_url: DatabaseFactory) -> str:
    """One fresh, fully migrated platform database for this test."""
    return new_database_url()


@pytest.fixture
def content_database_url(new_content_database_url: DatabaseFactory) -> str:
    """One fresh, fully migrated content database for this test."""
    return new_content_database_url()


def build_test_container(database_url: str) -> Container:
    settings = Settings(
        _env_file=None,
        environment="test",
        provider_mode="fake",
        database_url=database_url,
        prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
    )
    return build_container(settings)


@pytest.fixture
def container(database_url: str) -> Iterator[Container]:
    """Empty registry: nothing is published."""
    result = build_test_container(database_url)
    yield result
    result.repository.engine.dispose()


def publish_with_simulated_reviews(store: ClinicalStore, bundle: ClinicalBundle) -> None:
    """Import, two simulated test approvals, publish. Never a human approval."""
    store.import_bundle(bundle)
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))
    store.publish(bundle.scenarios[0].id, bundle.scenarios[0].version, actor="ISOLATED TEST")


@pytest.fixture
def published_container(database_url: str) -> Iterator[Container]:
    """One synthetic voice scenario published through the real path."""
    result = build_test_container(database_url)
    publish_with_simulated_reviews(result.cases.store, synthetic_bundle())
    yield result
    result.repository.engine.dispose()


def build_content_test_container(content_database_url: str, storage_dir: Path) -> ContentContainer:
    settings = Settings(
        _env_file=None,
        environment="test",
        provider_mode="fake",
        content_database_url=content_database_url,
        content_storage_dir=storage_dir,
        prompt_directory=PROJECT_ROOT / "backend" / "src" / "ari" / "prompts",
    )
    return build_content_container(settings)


@pytest.fixture
def content_container(content_database_url: str, tmp_path: Path) -> Iterator[ContentContainer]:
    """The content pipeline on a fresh content database, fake model, files under tmp_path."""
    result = build_content_test_container(content_database_url, tmp_path / "content")
    yield result
    result.repository.engine.dispose()  # type: ignore[attr-defined]


@pytest.fixture
def practice_container(database_url: str) -> Iterator[Container]:
    """Two synthetic text exercises (arzt_arzt, fachbegriffe) published through the real path."""
    result = build_test_container(database_url)
    for phase in ("arzt_arzt", "fachbegriffe"):
        publish_with_simulated_reviews(result.cases.store, synthetic_practice(phase).bundle)
    yield result
    result.repository.engine.dispose()
