"""Every analysis of a session is kept as an immutable run; `evaluations` points at the current."""

from __future__ import annotations

import asyncio
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from types import ModuleType
from typing import Any, TypeVar

import pytest
from accounts_fixtures import sign_in
from alembic import command
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ari.api.app import create_app
from ari.application.contracts import LLMRequest, ProviderResult
from ari.application.prompting import load_prompt
from ari.application.schemas import EvaluationOutputSchema
from ari.application.services.conversation import ConversationOrchestrator
from ari.application.services.evaluation import LLMBackedEvaluator
from ari.config import PROJECT_ROOT
from ari.container import Container
from ari.domain.errors import InvalidStateError, ProviderError
from ari.domain.models import (
    CEFRLevel,
    ExecutionRecord,
    ExecutionStatus,
    SessionStatus,
    VocabularyObservation,
    VocabularyState,
    new_id,
)
from ari.infrastructure.persistence.platform.repository import (
    EvaluationRow,
    EvaluationRunRow,
)
from ari.infrastructure.persistence.platform.schema import alembic_config
from ari.infrastructure.providers.fake import FakeLLMProvider

T = TypeVar("T", bound=BaseModel)
MIGRATION = (
    PROJECT_ROOT / "backend" / "migrations" / "platform" / "versions" / "0004_evaluation_runs.py"
)


@pytest.fixture
def container(published_container: Container) -> Container:
    return published_container


async def _analysed_session(container: Container) -> str:
    learner = container.orchestrator.create_learner(CEFRLevel.B1)
    case = container.cases.list()[0]
    session = container.orchestrator.create_session(learner.id, case.id, case.version)
    container.orchestrator.activate(session.id)
    await container.orchestrator.process_transcript(session.id, "Seit wann haben Sie Schmerzen?")
    await container.orchestrator.end_session(session.id)
    return session.id


async def _reanalyse(container: Container, session_id: str) -> None:
    # The re-analysis path of the product: a failed analysis is retried.
    container.repository.set_status(session_id, SessionStatus.ANALYSIS_FAILED)
    await container.orchestrator.end_session(session_id)


def _runs(container: Container, session_id: str) -> list[EvaluationRunRow]:
    with Session(container.repository.engine) as db:
        return list(
            db.scalars(
                select(EvaluationRunRow)
                .where(EvaluationRunRow.session_id == session_id)
                .order_by(EvaluationRunRow.run_no)
            )
        )


def _pointer(container: Container, session_id: str) -> EvaluationRow:
    with Session(container.repository.engine) as db:
        row = db.get(EvaluationRow, session_id)
        assert row is not None
        return row


def _evaluation_execution(container: Container, session_id: str) -> ExecutionRecord:
    session = container.repository.get_session(session_id)
    return next(
        e
        for e in reversed(session.executions)
        if e.operation == "session_evaluation" and e.status is ExecutionStatus.SUCCEEDED
    )


@pytest.mark.asyncio
async def test_two_analyses_keep_two_runs_and_point_at_the_second(container: Container) -> None:
    session_id = await _analysed_session(container)
    await _reanalyse(container, session_id)

    runs = _runs(container, session_id)
    assert [run.run_no for run in runs] == [1, 2]
    pointer = _pointer(container, session_id)
    assert pointer.current_run_id == runs[1].id
    assert pointer.payload == runs[1].payload
    assert runs[0].payload["created_at"] != runs[1].payload["created_at"]
    session = container.repository.get_session(session_id)
    assert session.evaluation is not None
    assert session.evaluation.created_at.isoformat() == runs[1].payload["created_at"]
    # Each run names the call that produced it, with its exact provenance.
    executions = {e.id: e for e in session.executions}
    assert runs[0].execution_id != runs[1].execution_id
    for run in runs:
        assert run.execution_id is not None
        execution = executions[run.execution_id]
        assert execution.operation == "session_evaluation"
        assert execution.status is ExecutionStatus.SUCCEEDED
        assert run.prompt_version == execution.prompt_version == "evaluation-v9"
        assert run.prompt_hash == execution.prompt_hash
        assert run.prompt_hash
        assert run.model == execution.model
        assert run.model_returned == execution.model_returned == "deterministic-fake-v1"
        assert run.schema_version == run.payload["schema_version"]
        assert run.rubric_version == run.payload["rubric_version"]
        # Dated by the analysis itself, like the backfilled runs.
        assert run.created_at == datetime.fromisoformat(run.payload["created_at"])


class FailingEvaluationProvider:
    """A provider timing out on the evaluation, after a first analysis succeeded."""

    def __init__(self) -> None:
        self.fake = FakeLLMProvider()

    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]:
        result = await self.fake.generate_structured(request, response_model)
        if response_model is EvaluationOutputSchema:
            raise ProviderError(
                "timeout",
                execution=replace(
                    result.execution,
                    status=ExecutionStatus.FAILED,
                    error_code="TimeoutError",
                    error_message="timeout",
                    retryable=True,
                ),
            )
        return result


@pytest.mark.asyncio
async def test_a_failed_reanalysis_adds_no_run_and_keeps_the_current_one(
    container: Container,
) -> None:
    session_id = await _analysed_session(container)
    before = container.repository.get_session(session_id)
    failing = ConversationOrchestrator(
        container.repository,
        container.cases,
        container.orchestrator._patient,
        LLMBackedEvaluator(
            FailingEvaluationProvider(),
            load_prompt(
                PROJECT_ROOT / "backend" / "src" / "ari" / "prompts" / "evaluation_v9.txt",
                "evaluation-v9",
            ),
            "fr-FR",
        ),
        container.voice_stack,
    )
    container.repository.set_status(session_id, SessionStatus.ANALYSIS_FAILED)

    with pytest.raises(ProviderError):
        await failing.end_session(session_id)

    # A technical error is traced as a failed call, never as an analysis of the learner.
    runs = _runs(container, session_id)
    assert [run.run_no for run in runs] == [1]
    assert _pointer(container, session_id).current_run_id == runs[0].id
    after = container.repository.get_session(session_id)
    assert after.status is SessionStatus.ANALYSIS_FAILED
    assert after.evaluation == before.evaluation
    assert any(e.error_code == "TimeoutError" for e in after.executions)
    # The next successful retry becomes run 2.
    await container.orchestrator.end_session(session_id)
    assert [run.run_no for run in _runs(container, session_id)] == [1, 2]


@pytest.mark.asyncio
async def test_a_run_needs_the_succeeded_call_of_its_own_session(container: Container) -> None:
    session_id = await _analysed_session(container)
    session = container.repository.get_session(session_id)
    assert session.evaluation is not None and session.metrics is not None
    execution = _evaluation_execution(container, session_id)

    def save(candidate: ExecutionRecord) -> None:
        container.repository.save_analysis(
            session_id,
            session.evaluation,
            session.metrics,
            (),
            candidate,  # type: ignore[arg-type]
        )

    def recorded(**changes: Any) -> ExecutionRecord:
        call = replace(execution, id=new_id(), **changes)
        container.repository.record_execution(call)
        return call

    with pytest.raises(InvalidStateError, match="never recorded"):
        save(replace(execution, id=new_id()))
    with pytest.raises(InvalidStateError, match="failed execution"):
        save(recorded(status=ExecutionStatus.FAILED))
    other = await _analysed_session(container)
    with pytest.raises(InvalidStateError, match="another session"):
        save(recorded(session_id=other))
    with pytest.raises(InvalidStateError, match="not a session evaluation"):
        save(recorded(operation="patient_simulation"))
    with pytest.raises(InvalidStateError, match="prompt"):
        save(recorded(prompt_version="evaluation-v0"))
    # The recorded row is checked, not the object passed in: a failed call stays failed.
    failed = recorded(status=ExecutionStatus.FAILED, error_code="TimeoutError")
    with pytest.raises(InvalidStateError, match="failed execution"):
        save(replace(failed, status=ExecutionStatus.SUCCEEDED))
    assert len(_runs(container, session_id)) == 1


@pytest.mark.asyncio
async def test_a_failure_after_the_run_is_written_rolls_back_the_run_and_the_pointer(
    container: Container,
) -> None:
    session_id = await _analysed_session(container)
    session = container.repository.get_session(session_id)
    assert session.evaluation is not None and session.metrics is not None
    call = replace(_evaluation_execution(container, session_id), id=new_id())
    container.repository.record_execution(call)
    # Fails at commit, after the run and the pointer are flushed.
    orphan = VocabularyObservation(
        id=new_id(),
        session_id="no-such-session",
        lemma="x",
        translation="x",
        example="x",
        evidence_turn_sequences=(1,),
        state=VocabularyState.IDENTIFIED,
        confidence=1.0,
    )

    with pytest.raises(IntegrityError):
        container.repository.save_analysis(
            session_id, session.evaluation, session.metrics, (orphan,), call
        )

    runs = _runs(container, session_id)
    assert [run.run_no for run in runs] == [1]
    assert _pointer(container, session_id).current_run_id == runs[0].id


@pytest.mark.asyncio
async def test_concurrent_analyses_get_distinct_run_numbers(container: Container) -> None:
    session_id = await _analysed_session(container)
    session = container.repository.get_session(session_id)
    assert session.evaluation is not None and session.metrics is not None
    execution = _evaluation_execution(container, session_id)
    calls = [replace(execution, id=new_id()) for _ in range(4)]
    for call in calls:
        container.repository.record_execution(call)

    def save(call: ExecutionRecord) -> None:
        container.repository.save_analysis(
            session_id,
            session.evaluation,
            session.metrics,
            (),
            call,  # type: ignore[arg-type]
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, calls))

    runs = _runs(container, session_id)
    assert [run.run_no for run in runs] == [1, 2, 3, 4, 5]
    assert _pointer(container, session_id).current_run_id == runs[-1].id


@pytest.mark.asyncio
async def test_runs_cannot_be_updated_or_deleted(container: Container) -> None:
    session_id = await _analysed_session(container)
    run_id = _runs(container, session_id)[0].id
    for statement in (
        "UPDATE evaluation_runs SET payload = '{}'::jsonb WHERE id = :id",
        "UPDATE evaluation_runs SET model = 'other' WHERE id = :id",
        "DELETE FROM evaluation_runs WHERE id = :id",
    ):
        with (
            container.repository.engine.begin() as connection,
            pytest.raises(IntegrityError, match="Immutable evaluation run"),
        ):
            connection.execute(text(statement), {"id": run_id})
    assert _runs(container, session_id)[0].id == run_id


@pytest.mark.asyncio
async def test_the_pointer_and_run_numbers_are_constrained(container: Container) -> None:
    first = await _analysed_session(container)
    second = await _analysed_session(container)
    other_run = _runs(container, second)[0].id
    # Numbering is per session: the second session's first analysis is its run 1.
    assert [run.run_no for run in _runs(container, second)] == [1]
    for statement, parameters in (
        # The current run must belong to the same session.
        (
            "UPDATE evaluations SET current_run_id = :run WHERE session_id = :session",
            {"run": other_run, "session": first},
        ),
        # Run numbers are unique per session and start at 1.
        (
            "INSERT INTO evaluation_runs (id, session_id, run_no, payload, schema_version, "
            "prompt_version, rubric_version, created_at) "
            "VALUES (:id, :session, 1, '{}'::jsonb, 's', 'p', 'r', now())",
            {"id": new_id(), "session": first},
        ),
        (
            "INSERT INTO evaluation_runs (id, session_id, run_no, payload, schema_version, "
            "prompt_version, rubric_version, created_at) "
            "VALUES (:id, :session, 0, '{}'::jsonb, 's', 'p', 'r', now())",
            {"id": new_id(), "session": first},
        ),
        # Every evaluation points at a run.
        (
            "UPDATE evaluations SET current_run_id = NULL WHERE session_id = :session",
            {"session": first},
        ),
    ):
        with (
            container.repository.engine.begin() as connection,
            pytest.raises(IntegrityError),
        ):
            connection.execute(text(statement), parameters)


def test_the_session_api_is_unchanged_by_a_second_run(container: Container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        case = client.get("/api/cases").json()[0]
        sign_in(client)
        learner = client.post("/api/learners", json={"target_cefr": "C1"}).json()
        session_id = client.post(
            "/api/sessions",
            json={
                "learner_id": learner["id"],
                "case_id": case["id"],
                "case_version": case["version"],
            },
        ).json()["id"]
        container.orchestrator.activate(session_id)
        asyncio.run(container.orchestrator.process_transcript(session_id, "Haben Sie Fieber?"))
        first = client.post(f"/api/sessions/{session_id}/end", json={}).json()
        container.repository.set_status(session_id, SessionStatus.ANALYSIS_FAILED)
        retried = client.post(f"/api/sessions/{session_id}/analysis/retry")
        assert retried.status_code == 200
        second = client.get(f"/api/sessions/{session_id}").json()

    assert second == retried.json()
    assert second.keys() == first.keys()
    assert second["status"] == first["status"] == "completed"
    run = _runs(container, session_id)[-1]
    assert run.run_no == 2
    assert second["evaluation"]["created_at"] == run.payload["created_at"]
    # The deterministic fake judges the same transcript the same way: only the date moves.
    assert {**second["evaluation"], "created_at": None} == {
        **first["evaluation"],
        "created_at": None,
    }


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0004", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COPY_EXECUTION = (
    "INSERT INTO executions (id, session_id, operation, provider, model, status, "
    "prompt_version, prompt_hash, case_version, case_hash, latency_ms, usage, retryable, "
    "created_at, learner_id, attempt_ref, cost_unknown) "
    "SELECT :new, session_id, operation, provider, model, :status, prompt_version, prompt_hash, "
    "case_version, case_hash, latency_ms, usage, retryable, created_at, learner_id, attempt_ref, "
    "cost_unknown FROM executions WHERE id = :old"
)


def _backfilled(container: Container) -> dict[str, tuple[Any, ...]]:
    with container.repository.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT r.session_id, r.id, r.run_no, r.execution_id, r.model, r.prompt_hash, "
                "r.prompt_version, r.schema_version, r.rubric_version, r.created_at, "
                "e.current_run_id = r.id, e.payload = r.payload "
                "FROM evaluation_runs r JOIN evaluations e USING (session_id)"
            )
        ).all()
    return {row[0]: tuple(row[1:]) for row in rows}


@pytest.mark.asyncio
async def test_backfill_turns_each_evaluation_into_run_one_idempotently(
    container: Container, database_url: str
) -> None:
    unique = await _analysed_session(container)
    ambiguous = await _analysed_session(container)
    mismatched = await _analysed_session(container)
    unanalysed = container.orchestrator.create_session(
        container.orchestrator.create_learner(CEFRLevel.B1).id,
        container.cases.list()[0].id,
        container.cases.list()[0].version,
    ).id
    sessions = (unique, ambiguous, mismatched)
    evaluations = {sid: container.repository.get_session(sid).evaluation for sid in sessions}
    calls = {sid: _evaluation_execution(container, sid) for sid in sessions}
    container.repository.engine.dispose()
    config = alembic_config(database_url)
    # Back to the single-row schema of 0003: `evaluations` keeps the analysis, no run exists.
    command.downgrade(config, "0003_execution_costs")
    with container.repository.engine.begin() as connection:
        # A failed call does not make the only succeeded one ambiguous...
        connection.execute(
            text(COPY_EXECUTION), {"new": new_id(), "status": "failed", "old": calls[unique].id}
        )
        # ...a second succeeded call does: which one produced the analysis is unknown.
        connection.execute(
            text(COPY_EXECUTION),
            {"new": new_id(), "status": "succeeded", "old": calls[ambiguous].id},
        )
        # So does an analysis whose prompt is not the one of the only call.
        connection.execute(
            text(
                "UPDATE evaluations SET payload = jsonb_set(payload, '{prompt_version}', "
                "'\"evaluation-v5\"') WHERE session_id = :id"
            ),
            {"id": mismatched},
        )
    container.repository.engine.dispose()

    command.upgrade(config, "head")

    after = _backfilled(container)
    assert set(after) == set(sessions)
    assert unanalysed not in after
    for sid in sessions:
        run_id, run_no, *_, prompt, schema, rubric, created, current, same = after[sid]
        evaluation = evaluations[sid]
        assert evaluation is not None
        assert run_id == f"backfill:{sid}"
        assert run_no == 1 and current and same
        assert schema == evaluation.schema_version and rubric == evaluation.rubric_version
        assert created == evaluation.created_at
        if sid != mismatched:
            assert prompt == evaluation.prompt_version
            # Read compatibility: the repository reads the backfilled run as it read the row.
            assert container.repository.get_session(sid).evaluation == evaluation
    assert after[unique][2:5] == (calls[unique].id, calls[unique].model, calls[unique].prompt_hash)
    assert after[ambiguous][2:5] == (None, None, None)
    assert after[mismatched][2:5] == (None, None, None)
    assert after[mismatched][5] == "evaluation-v5"

    # Replaying the backfill adds nothing and moves nothing.
    migration = _migration()
    with container.repository.engine.begin() as connection:
        connection.execute(text(migration.BACKFILL_RUNS))
        connection.execute(text(migration.BACKFILL_POINTERS))
    assert _backfilled(container) == after
    # Neither does a downgrade followed by a new upgrade.
    container.repository.engine.dispose()
    command.downgrade(config, "0003_execution_costs")
    command.upgrade(config, "head")
    assert _backfilled(container) == after
    command.check(config)
    # The first analysis after the migration is run 2.
    await _reanalyse(container, unique)
    runs = _runs(container, unique)
    assert [run.run_no for run in runs] == [1, 2]
    # A late replay of the backfill never moves the pointer back to run 1.
    with container.repository.engine.begin() as connection:
        connection.execute(text(migration.BACKFILL_RUNS))
        connection.execute(text(migration.BACKFILL_POINTERS))
    assert [run.run_no for run in _runs(container, unique)] == [1, 2]
    assert _pointer(container, unique).current_run_id == runs[1].id


@pytest.mark.asyncio
async def test_downgrade_keeps_the_current_analysis(
    container: Container, database_url: str
) -> None:
    session_id = await _analysed_session(container)
    await _reanalyse(container, session_id)
    current = _runs(container, session_id)[-1]
    container.repository.engine.dispose()
    config = alembic_config(database_url)

    command.downgrade(config, "0003_execution_costs")
    with container.repository.engine.connect() as connection:
        payload = connection.execute(
            text("SELECT payload FROM evaluations WHERE session_id = :id"), {"id": session_id}
        ).scalar_one()
    assert payload == current.payload
    container.repository.engine.dispose()

    # Run 1 is lost, as documented; the analysis shown to the learner is not.
    command.upgrade(config, "head")
    runs = _runs(container, session_id)
    assert [(run.run_no, run.payload) for run in runs] == [(1, current.payload)]
    # Two succeeded calls: the backfill does not guess which one it was.
    assert runs[0].execution_id is None
