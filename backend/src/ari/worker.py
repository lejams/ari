"""Background worker of the content pipeline (`python -m ari.worker`).

Claims one job at a time from the PostgreSQL queue, runs its handler, records every model
call, retries with exponential backoff and stops cleanly on SIGTERM after the current job.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import socket
from datetime import timedelta
from os import getpid

from ari.config import Settings
from ari.content.container import ContentContainer, build_content_container
from ari.content.domain.documents import Job, JobType
from ari.content.services.ai import ModelCallFailed

log = logging.getLogger("ari.worker")
STALE_LOCK = timedelta(minutes=30)
BACKOFF_BASE_SECONDS = 30


def default_worker_id(settings: Settings) -> str:
    return settings.worker_id or f"{socket.gethostname()}-{getpid()}"


def retry_delay(job: Job) -> timedelta | None:
    """Exponential backoff while attempts remain; None means the job is dead."""
    if job.attempts >= job.max_attempts:
        return None
    return timedelta(seconds=BACKOFF_BASE_SECONDS * 2 ** (job.attempts - 1))


async def run_one(
    container: ContentContainer, worker_id: str, types: list[JobType] | None = None
) -> Job | None:
    """Claim and run one job; returns it, or None when the queue is empty."""
    job = container.queue.claim(worker_id, types)
    if job is None:
        return None
    log.info("job %s %s attempt %s", job.type.value, job.id, job.attempts)
    try:
        await container.handlers[job.type](job)
    except ModelCallFailed as exc:
        with container.repository.transaction() as tx:
            tx.add_ai_run(exc.run)
        _fail(container, job, exc, retryable=exc.run.retryable)
    except Exception as exc:
        log.exception("job %s failed", job.id)
        _fail(container, job, exc, retryable=not isinstance(exc, NotImplementedError))
    else:
        container.queue.succeed(job.id)
    return container.queue.get(job.id)


def _fail(container: ContentContainer, job: Job, error: Exception, *, retryable: bool) -> None:
    delay = retry_delay(job) if retryable else None
    container.queue.fail(job.id, f"{type(error).__name__}: {error}", retry_in=delay)


async def serve(container: ContentContainer, worker_id: str, *, once: bool) -> int:
    reclaimed = container.queue.reclaim_stale(STALE_LOCK)
    if reclaimed:
        log.warning("reclaimed %s stale job(s)", reclaimed)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    processed = 0
    while not stopping.is_set():
        job = await run_one(container, worker_id)
        if job is not None:
            processed += 1
            continue
        if once:
            break
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=container.settings.worker_poll_seconds)
    return processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Drain the queue then exit")
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    container = build_content_container(settings)
    worker_id = args.worker_id or default_worker_id(settings)
    processed = asyncio.run(serve(container, worker_id, once=args.once))
    log.info("processed %s job(s)", processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
