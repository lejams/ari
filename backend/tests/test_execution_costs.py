"""Every recorded model call carries its cost, its learner and its attempt."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_backoffice_api import signed_in

from ari.application.contracts import ExecutionContext
from ari.backoffice_api.app import create_backoffice_app
from ari.backoffice_api.container import Backoffice
from ari.container import Container
from ari.content.container import ContentContainer
from ari.content.domain.accounts import Role
from ari.content.domain.documents import AiRun
from ari.domain.models import (
    ConversationSession,
    ExecutionRecord,
    ExecutionStatus,
    LearnerProfile,
    LearningGoal,
    new_id,
)
from ari.infrastructure.persistence.content.rows import AiRunRow
from ari.infrastructure.persistence.platform.repository import ExecutionRow, SqlSessionRepository
from ari.infrastructure.pricing import load_pricing
from ari.infrastructure.providers.openai.tts import OpenAIStreamingTTSProvider


def _execution(
    session_id: str, provider: str, model: str, usage: dict[str, Any]
) -> ExecutionRecord:
    return ExecutionRecord(
        id=new_id(),
        session_id=session_id,
        operation="session_evaluation",
        provider=provider,
        model=model,
        status=ExecutionStatus.SUCCEEDED if usage else ExecutionStatus.FAILED,
        prompt_version="evaluation-v6",
        prompt_hash="h",
        case_version="1",
        case_hash="h",
        latency_ms=5,
        usage=usage,
    )


def test_session_calls_are_costed_and_attributed_to_the_learner(container: Container) -> None:
    repository = container.repository
    learner = repository.create_learner(LearnerProfile(id=new_id(), goal=LearningGoal()))
    session = repository.create_session(
        ConversationSession(
            id=new_id(),
            learner_id=learner.id,
            case_id="case",
            case_version="1",
            case_hash="hash",
            goal=LearningGoal(),
        )
    )
    fake = _execution(session.id, "fake", "deterministic-fake-v1", {"input_characters": 3})
    priced = _execution(
        session.id, "openai", "gpt-6-luna", {"prompt_tokens": 10_000, "completion_tokens": 2_000}
    )
    failed = _execution(session.id, "openai", "gpt-6-luna", {})
    for execution in (fake, priced, failed):
        repository.record_execution(execution)

    with Session(repository.engine) as db:
        rows = {row.id: row for row in db.scalars(select(ExecutionRow))}
    for row in rows.values():
        assert row.learner_id == learner.id
        assert row.attempt_ref == f"session:{session.id}"
        assert row.pricing_version == "pricing-v1"
    assert rows[fake.id].cost_micro_eur == 0 and rows[fake.id].cost_unknown == []
    # 10k input x 0.10 $/M + 2k output x 0.50 $/M = 0.002 $, at 0.88 €/$.
    assert rows[priced.id].cost_micro_eur == round(0.002 * 0.88 * 1_000_000)
    # A failed call that reported no usage is unknown, not free.
    assert rows[failed.id].cost_micro_eur is None
    assert rows[failed.id].cost_unknown == ["usage.tokens"]


def test_pipeline_calls_are_costed(content_container: ContentContainer) -> None:
    run = AiRun(
        id=new_id(),
        operation="protocol_extraction",
        provider="openai",
        model="gpt-6-luna",
        schema_name="ExtractionOutput",
        input_hash="0" * 64,
        status="succeeded",
        latency_ms=10,
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 100_000},
    )
    unpriced = AiRun(
        id=new_id(),
        operation="protocol_extraction",
        provider="openai",
        model="model-without-price",
        schema_name="ExtractionOutput",
        input_hash="0" * 64,
        status="succeeded",
        latency_ms=10,
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    with content_container.repository.transaction() as tx:
        tx.add_ai_run(run)
        tx.add_ai_run(unpriced)
    engine = content_container.repository.engine  # type: ignore[attr-defined]
    with Session(engine) as db:
        rows = {row.id: row for row in db.scalars(select(AiRunRow))}
    assert rows[run.id].cost_micro_eur == round((0.10 + 0.05) * 0.88 * 1_000_000)
    assert rows[run.id].pricing_version == "pricing-v1"
    assert rows[unpriced.id].cost_micro_eur is None
    assert rows[unpriced.id].cost_unknown == ["price:openai/model-without-price"]


class _StreamingResponse:
    async def iter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        for _ in range(3):
            yield b"\x00" * 16_000  # 3 x 16 000 bytes of PCM16 at 24 kHz = 1 second


class _StreamingSpeech:
    @asynccontextmanager
    async def create(self, **kwargs: object) -> AsyncIterator[_StreamingResponse]:
        del kwargs
        yield _StreamingResponse()


class _Speech:
    with_streaming_response = _StreamingSpeech()


class _Audio:
    speech = _Speech()


class _OfflineClient:
    audio = _Audio()


@pytest.mark.asyncio
async def test_openai_tts_reports_the_synthesized_audio_duration() -> None:
    provider = OpenAIStreamingTTSProvider("key", model="gpt-4o-mini-tts", timeout_seconds=5)
    provider._client = _OfflineClient()  # type: ignore[assignment]
    context = ExecutionContext(
        session_id="s", operation="text_to_speech", case_version="1", case_hash="h"
    )
    events = [event async for event in provider.stream("Guten Tag", context)]
    execution = events[-1].execution
    assert execution is not None
    assert execution.usage["audio_seconds"] == 1.0
    assert execution.usage["characters"] == len("Guten Tag")


def test_owner_sees_costs_with_unknown_costs_counted_apart(
    backoffice: Backoffice, database_url: str
) -> None:
    repository = SqlSessionRepository(
        database_url, pricing=load_pricing(backoffice.settings.pricing_path)
    )
    learner = repository.create_learner(LearnerProfile(id=new_id(), goal=LearningGoal()))
    session = repository.create_session(
        ConversationSession(
            id=new_id(),
            learner_id=learner.id,
            case_id="case",
            case_version="1",
            case_hash="hash",
            goal=LearningGoal(),
        )
    )
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    repository.record_execution(_execution(session.id, "openai", "gpt-6-luna", usage))
    repository.record_execution(_execution(session.id, "openai", "unpriced-model", usage))
    repository.engine.dispose()
    with backoffice.content.repository.transaction() as tx:
        tx.add_ai_run(
            AiRun(
                id=new_id(),
                operation="bundle_draft",
                provider="openai",
                model="gpt-6-luna",
                schema_name="BundleDraftOutput",
                input_hash="0" * 64,
                status="succeeded",
                latency_ms=1,
                usage=usage,
            )
        )

    app = create_backoffice_app(backoffice)
    owner = signed_in(app, backoffice, "owner@example.org", "Owner", Role.OWNER)
    report = owner.get("/api/costs?days=7").json()
    luna = round((0.10 + 0.50) * 0.88 * 1_000_000)
    learners = report["learners"]
    assert learners["total"] == {"calls": 2, "known_micro_eur": luna, "unknown_calls": 1}
    assert learners["unknown_reasons"] == {"price:openai/unpriced-model": 1}
    assert learners["by_learner"][0]["learner_id"] == learner.id
    # One active learner: they are their own median. A ratio informs, nothing is cut on it.
    assert learners["by_learner"][0]["ratio_to_median"] == 1.0
    assert learners["median_learner_micro_eur"] == luna
    assert learners["recent_sessions"][0]["session_id"] == session.id
    assert {row["kind"] for row in learners["by_kind"]} == {"session"}
    assert report["content"]["total"] == {"calls": 1, "known_micro_eur": luna, "unknown_calls": 0}

    physician = signed_in(app, backoffice, "doc@example.org", "Doc", Role.PHYSICIAN_REVIEWER)
    assert physician.get("/api/costs").status_code == 403
