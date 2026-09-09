"""Opt-in real PostgreSQL test against an explicitly disposable local/CI database."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from clinical_fixtures import simulated_review, synthetic_bundle
from freiburg_fixtures import synthetic_freiburg
from sqlalchemy import create_engine, inspect, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from ari.config import PROJECT_ROOT
from ari.domain.errors import InvalidStateError
from ari.domain.models import ConversationSession, LearnerProfile, LearningGoal
from ari.infrastructure.cases.clinical_store import ClinicalStore
from ari.infrastructure.cases.freiburg_import import FreiburgDraftStore
from ari.infrastructure.persistence.clinical_rows import ClinicalCaseRow, SourceRow
from ari.infrastructure.persistence.sqlite import SqliteSessionRepository


def test_real_postgres_draft_intake(
    monkeypatch: pytest.MonkeyPatch,
    postgres_url: str,
) -> None:
    monkeypatch.setenv("ARI_DATABASE_URL", postgres_url)
    config = Config(PROJECT_ROOT / "alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    engine = SqliteSessionRepository(postgres_url).engine
    column = next(
        c for c in inspect(engine).get_columns("clinical_draft_batches") if c["name"] == "payload"
    )
    assert isinstance(column["type"], JSONB)
    store, export = FreiburgDraftStore(engine), synthetic_freiburg()
    assert store.import_export(export, {}, dry_run=True)["cas_nouveaux"] == 1
    assert store.inspect()["cas_importes"] == 0
    assert store.import_export(export, {})["cas_importes_cette_operation"] == 1
    assert store.import_export(export, {})["cas_identiques"] == 1
    assert store.inspect()["cas"][0]["material"]["case"]["demographics"]["age_years"] is None
    with pytest.raises(DBAPIError), engine.begin() as db:
        db.execute(text("UPDATE clinical_draft_cases SET status='published'"))
    with pytest.raises(DBAPIError), engine.begin() as db:
        db.execute(text("DELETE FROM clinical_draft_batches"))
    with pytest.raises(DBAPIError), engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO clinical_draft_cases VALUES ('bad','1','hash','missing',"
                "'draft_unvalidated')"
            )
        )
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM clinical_scenarios")) == 0
    engine.dispose()


@pytest.fixture
def postgres_url() -> Iterator[str]:
    url = os.environ.get("ARI_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("No explicitly disposable PostgreSQL database configured")
    parsed = make_url(url)
    if not (parsed.database or "").startswith("ari_goal4_test"):
        pytest.fail("PostgreSQL test requires a disposable ari_goal4_test* database")
    if parsed.host not in (None, "localhost", "127.0.0.1", "::1"):
        pytest.fail("Only an isolated local PostgreSQL server is allowed")
    query_host = str(parsed.query.get("host", ""))
    if query_host and not query_host.startswith("/"):
        pytest.fail("Query host must be an explicit local socket directory")
    schema = "ari_goal4_" + uuid4().hex
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = parsed.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield isolated.render_as_string(hide_password=False)
    finally:
        # The name is generated above, never derived from user input; only test-owned data.
        with engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_real_postgres_jsonb_migration_and_workflow(
    monkeypatch: pytest.MonkeyPatch,
    postgres_url: str,
) -> None:
    url = postgres_url
    monkeypatch.setenv("ARI_DATABASE_URL", url)
    config = Config(PROJECT_ROOT / "alembic.ini")
    command.upgrade(config, "20260903_0002")
    repo = SqliteSessionRepository(url)
    learner = repo.create_learner(LearnerProfile(id="pg-historical", goal=LearningGoal()))
    historic = ConversationSession(
        id="pg-original-session",
        learner_id=learner.id,
        case_id="legacy",
        case_version="1",
        case_hash="original-hash",
        goal=learner.goal,
    )
    repo.create_session(historic)
    with repo.engine.begin() as db:
        for identifier, amount in (("known-cost", 0.02), ("unknown-cost", None)):
            db.execute(
                text("""INSERT INTO executions (
                id, session_id, operation, provider, model, status, case_version, case_hash,
                latency_ms, usage, estimated_cost_usd, pricing_version, retryable, created_at
            ) VALUES (:id, 'pg-original-session', 'patient_response', 'fake', 'legacy',
                'succeeded', '1', 'original-hash', 12, '{}', :amount, 'legacy-pricing',
                FALSE, CURRENT_TIMESTAMP)"""),
                {"id": identifier, "amount": amount},
            )
    command.upgrade(config, "20260903_0003")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)
    with repo.engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM learners WHERE id='pg-historical'")) == 1
        records = db.execute(
            text(
                "SELECT id, cost_assumptions, cost_amount_usd, case_hash "
                "FROM executions ORDER BY id"
            )
        ).all()
        assert records == [
            (
                "known-cost",
                ["legacy_estimated_cost_without_structured_units"],
                0.02,
                "original-hash",
            ),
            ("unknown-cost", [], None, "original-hash"),
        ]
    assert repo.get_session(historic.id).case_hash == historic.case_hash
    column = next(
        c for c in inspect(repo.engine).get_columns("clinical_cases") if c["name"] == "payload"
    )
    assert isinstance(column["type"], JSONB)
    store, bundle = ClinicalStore(repo.engine), synthetic_bundle()
    assert store.import_bundle(bundle, dry_run=True)["cas_nouveaux"] == 1
    store.import_bundle(bundle)
    assert store.import_bundle(bundle)["cas_identiques"] == 1
    assert store.inspect("synthetic-scenario", "1")["bundle"] == bundle.model_dump(mode="json")
    for kind in ("clinical", "linguistic"):
        store.record_review(simulated_review(bundle, kind))

    def publish() -> bool:
        try:
            store.publish("synthetic-scenario", "1", actor="SIMULATED TEST")
            return True
        except InvalidStateError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: publish(), range(2))) == [False, True]
    for version in ("2", "3"):
        successor = bundle.scenarios[0].model_copy(update={"version": version})
        next_bundle = bundle.model_copy(update={"scenarios": (successor,)})
        store.import_bundle(next_bundle)
        for kind in ("clinical", "linguistic"):
            store.record_review(simulated_review(next_bundle, kind))
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(lambda v: store.publish("synthetic-scenario", v, actor="TEST"), ("2", "3"))
        )
    with repo.engine.connect() as db:
        assert (
            db.scalar(text("SELECT count(*) FROM clinical_scenarios WHERE status='published'")) == 1
        )
        assert db.scalar(text("SELECT count(*) FROM clinical_publication_events")) == 5
    with pytest.raises(DBAPIError), repo.engine.begin() as db:
        db.execute(update(ClinicalCaseRow).values(payload={}))
    with pytest.raises(DBAPIError), repo.engine.begin() as db:
        db.execute(update(SourceRow).values(content_hash="0" * 64))
    with pytest.raises(DBAPIError), repo.engine.begin() as db:
        db.execute(text("INSERT INTO clinical_case_sources VALUES ('bad', '1', 'missing')"))
    learner = repo.create_learner(LearnerProfile(id="pg-test-learner", goal=LearningGoal()))
    session = ConversationSession(
        id="pg-legacy-compatible-session",
        learner_id=learner.id,
        case_id="legacy",
        case_version="1",
        case_hash="legacy-hash",
        goal=learner.goal,
    )
    repo.create_session(session)
    assert repo.get_session(session.id).training_snapshot == {}
