"""The job queue as a PostgreSQL table, claimed with FOR UPDATE SKIP LOCKED."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, case, select, update
from sqlalchemy.orm import Session

from ari.content.domain.documents import Job, JobStatus, JobType
from ari.domain.errors import NotFoundError
from ari.domain.models import new_id
from ari.infrastructure.persistence.content.rows import JobRow

RETRYABLE_STATUSES = (JobStatus.QUEUED.value, JobStatus.FAILED.value)

# A document must pass the short, document-level stages before its potentially large fan-out
# of protocol extraction jobs. Values are explicit; FIFO still applies within each stage.
JOB_TYPE_PRIORITY = {
    JobType.EXTRACT_TEXT.value: 0,
    JobType.SEGMENT_DOCUMENT.value: 1,
    JobType.EXTRACT_PROTOCOL.value: 2,
    JobType.GENERATE_BUNDLE_DRAFT.value: 3,
}


def _dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _job(row: JobRow) -> Job:
    return Job(
        id=row.id,
        type=JobType(row.type),
        payload=dict(row.payload),
        status=JobStatus(row.status),
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        available_at=_dt(row.available_at) or datetime.now(UTC),
        locked_at=_dt(row.locked_at),
        locked_by=row.locked_by,
        last_error=row.last_error,
        document_id=row.document_id,
        protocol_id=row.protocol_id,
        created_at=_dt(row.created_at) or datetime.now(UTC),
        finished_at=_dt(row.finished_at),
    )


class PostgresJobQueue:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def enqueue(
        self,
        type: JobType,
        payload: dict[str, object],
        *,
        document_id: str | None = None,
        protocol_id: str | None = None,
    ) -> Job:
        now = datetime.now(UTC)
        row = JobRow(
            id=new_id(),
            type=type.value,
            payload=payload,
            status=JobStatus.QUEUED.value,
            attempts=0,
            max_attempts=3,
            available_at=now,
            document_id=document_id,
            protocol_id=protocol_id,
            created_at=now,
        )
        with Session(self.engine) as db, db.begin():
            db.add(row)
            db.flush()
            return _job(row)

    def claim(self, worker_id: str, types: Sequence[JobType] | None = None) -> Job | None:
        """Take the highest-priority runnable stage, FIFO within that stage."""
        now = datetime.now(UTC)
        candidate = (
            select(JobRow.id)
            .where(JobRow.status.in_(RETRYABLE_STATUSES), JobRow.available_at <= now)
            .order_by(
                case(JOB_TYPE_PRIORITY, value=JobRow.type, else_=len(JOB_TYPE_PRIORITY)),
                JobRow.created_at,
                JobRow.id,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if types:
            candidate = candidate.where(JobRow.type.in_([t.value for t in types]))
        with Session(self.engine) as db, db.begin():
            job_id = db.scalar(candidate)
            if job_id is None:
                return None
            db.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(
                    status=JobStatus.RUNNING.value,
                    locked_by=worker_id,
                    locked_at=now,
                    attempts=JobRow.attempts + 1,
                )
            )
            row = db.get(JobRow, job_id, populate_existing=True)
            assert row is not None
            return _job(row)

    def succeed(self, job_id: str) -> None:
        with Session(self.engine) as db, db.begin():
            db.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(
                    status=JobStatus.SUCCEEDED.value,
                    finished_at=datetime.now(UTC),
                    locked_by=None,
                    locked_at=None,
                    last_error=None,
                )
            )

    def fail(self, job_id: str, error: str, *, retry_in: timedelta | None) -> Job:
        """With `retry_in` the job waits and runs again; without it, it is dead."""
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "last_error": error[:2000],
            "locked_by": None,
            "locked_at": None,
        }
        if retry_in is None:
            values |= {"status": JobStatus.DEAD.value, "finished_at": now}
        else:
            values |= {"status": JobStatus.FAILED.value, "available_at": now + retry_in}
        with Session(self.engine) as db, db.begin():
            db.execute(update(JobRow).where(JobRow.id == job_id).values(**values))
            return _job(self._row(db, job_id))

    def retry(self, job_id: str) -> Job:
        with Session(self.engine) as db, db.begin():
            row = self._row(db, job_id)
            row.status = JobStatus.QUEUED.value
            row.attempts = 0
            row.available_at = datetime.now(UTC)
            row.finished_at = None
            db.flush()
            return _job(row)

    def retry_failed(self, job_id: str) -> Job | None:
        """Atomically requeue only a job that is waiting for manual recovery."""
        with Session(self.engine) as db, db.begin():
            result = db.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.status.in_((JobStatus.FAILED.value, JobStatus.DEAD.value)),
                )
                .values(
                    status=JobStatus.QUEUED.value,
                    attempts=0,
                    available_at=datetime.now(UTC),
                    locked_by=None,
                    locked_at=None,
                    finished_at=None,
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                return None
            return _job(self._row(db, job_id))

    def get(self, job_id: str) -> Job:
        with Session(self.engine) as db:
            return _job(self._row(db, job_id))

    def list(self, status: JobStatus | None = None) -> tuple[Job, ...]:
        statement = select(JobRow).order_by(JobRow.created_at, JobRow.id)
        if status is not None:
            statement = statement.where(JobRow.status == status.value)
        with Session(self.engine) as db:
            return tuple(_job(row) for row in db.scalars(statement))

    def reclaim_stale(self, older_than: timedelta) -> int:
        """A worker that died mid-job leaves it `running`; give it back to the queue."""
        cutoff = datetime.now(UTC) - older_than
        with Session(self.engine) as db, db.begin():
            result = db.execute(
                update(JobRow)
                .where(JobRow.status == JobStatus.RUNNING.value, JobRow.locked_at < cutoff)
                .values(
                    status=JobStatus.QUEUED.value,
                    locked_by=None,
                    locked_at=None,
                    last_error="Reclaimed after a stale lock",
                )
            )
            return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    def _row(db: Session, job_id: str) -> JobRow:
        row = db.get(JobRow, job_id)
        if row is None:
            raise NotFoundError("Tâche inconnue")
        return row
