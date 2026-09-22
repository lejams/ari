"""The queue: claim, retry with backoff, dead after the last attempt, stale locks reclaimed."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from ari.content.container import ContentContainer
from ari.content.domain.documents import Job, JobStatus, JobType
from ari.worker import retry_delay, run_one, serve


def test_failures_back_off_then_die_and_manual_retry_revives(
    content_container: ContentContainer,
) -> None:
    calls = 0

    async def failing(job: Job) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    content_container.handlers[JobType.EXTRACT_TEXT] = failing
    queued = content_container.queue.enqueue(JobType.EXTRACT_TEXT, {"document_id": "x"})

    first = asyncio.run(run_one(content_container, "w1"))
    assert first is not None and first.status is JobStatus.FAILED and first.attempts == 1
    assert first.last_error == "RuntimeError: boom"
    assert first.available_at > datetime.now(UTC) + timedelta(seconds=20)
    # Not runnable yet: nothing to claim.
    assert asyncio.run(run_one(content_container, "w1")) is None
    assert retry_delay(first) == timedelta(seconds=30)  # 30 s, 60 s, then dead

    engine = content_container.repository.engine  # type: ignore[attr-defined]
    with engine.begin() as connection:  # make it due again, twice more
        connection.execute(text("UPDATE jobs SET available_at = now()"))
    second = asyncio.run(run_one(content_container, "w1"))
    with engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET available_at = now()"))
    third = asyncio.run(run_one(content_container, "w1"))
    assert second is not None and second.status is JobStatus.FAILED
    assert third is not None and third.status is JobStatus.DEAD and third.attempts == 3
    assert calls == 3

    revived = content_container.queue.retry(queued.id)
    assert (revived.status, revived.attempts) == (JobStatus.QUEUED, 0)


def test_stale_running_jobs_are_reclaimed_and_success_clears_the_lock(
    content_container: ContentContainer,
) -> None:
    done: list[str] = []

    async def ok(job: Job) -> None:
        done.append(job.id)

    content_container.handlers[JobType.EXTRACT_TEXT] = ok
    job = content_container.queue.enqueue(JobType.EXTRACT_TEXT, {"document_id": "x"})
    engine = content_container.repository.engine  # type: ignore[attr-defined]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE jobs SET status='running', locked_by='dead-worker', "
                "locked_at = now() - interval '2 hours'"
            )
        )
    assert asyncio.run(run_one(content_container, "w2")) is None  # running jobs are not claimed
    assert content_container.queue.reclaim_stale(timedelta(minutes=30)) == 1
    finished = asyncio.run(run_one(content_container, "w2"))
    assert finished is not None and finished.status is JobStatus.SUCCEEDED
    assert finished.locked_by is None and finished.finished_at is not None
    assert done == [job.id]


def test_serve_touches_the_heartbeat_file_each_iteration(
    content_container: ContentContainer, tmp_path: Path
) -> None:
    heartbeat = tmp_path / "worker-heartbeat"
    container = replace(
        content_container,
        settings=content_container.settings.model_copy(
            update={"worker_heartbeat_path": heartbeat}
        ),
    )
    assert not heartbeat.exists()
    asyncio.run(serve(container, "w1", once=True))
    assert heartbeat.exists()
