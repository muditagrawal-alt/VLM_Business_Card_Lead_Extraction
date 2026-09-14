"""The durable task queue, backed by the tasks table.

PostgreSQL is already a dependency, so it serves as the queue rather than
adding Redis and a broker to operate. `SELECT ... FOR UPDATE SKIP LOCKED` lets
several workers claim disjoint rows without coordinating, and because the queue
is a table, a restart loses nothing.

Crash safety comes from leases rather than acknowledgements. A claimed task
carries an expiry; if the worker dies mid-card, the lease lapses and the task
becomes claimable again. A crash therefore costs one retry, not a lost card.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from app.core.logging import get_logger
from app.models import Job, JobStatus, Task, TaskStatus

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


async def claim_tasks(
    session: AsyncSession,
    *,
    limit: int,
    lease_seconds: int,
    max_attempts: int,
) -> list[Task]:
    """Claim up to `limit` runnable tasks, marking them in progress.

    Runnable means queued, or previously claimed by a worker whose lease has
    since expired. Attempt count is incremented at claim time, not on failure,
    so a worker that dies without reporting anything still consumes an attempt
    and a poison card cannot be retried forever.
    """
    now = _now()
    stmt = (
        select(Task)
        .where(
            Task.attempts < max_attempts,
            (Task.status == TaskStatus.QUEUED)
            | ((Task.status == TaskStatus.PROCESSING) & (Task.lease_until < now)),
        )
        .order_by(Task.created_at)
        .limit(limit)
    )

    # SKIP LOCKED is what allows multiple workers to poll concurrently without
    # blocking each other. SQLite, used by the unit tests, has no such clause.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    tasks = list((await session.scalars(stmt)).all())
    if not tasks:
        return []

    lease_until = now + timedelta(seconds=lease_seconds)
    for task in tasks:
        task.status = TaskStatus.PROCESSING
        task.lease_until = lease_until
        task.attempts += 1

    await session.flush()
    return tasks


async def heartbeat(session: AsyncSession, task_id: UUID, *, lease_seconds: int) -> None:
    """Extend a lease for a card still being processed.

    A slow card on the CPU tier can outlive its lease, at which point another
    worker would pick up work already in flight and the card would be charged
    to the model twice.
    """
    await session.execute(
        update(Task)
        .where(Task.id == task_id, Task.status == TaskStatus.PROCESSING)
        .values(lease_until=_now() + timedelta(seconds=lease_seconds))
    )


async def mark_done(session: AsyncSession, task: Task) -> None:
    task.status = TaskStatus.DONE
    task.lease_until = None
    task.error = None
    task.finished_at = _now()
    await session.flush()
    await refresh_job_counters(session, task.job_id)


async def mark_failed(session: AsyncSession, task: Task, *, error: str, max_attempts: int) -> bool:
    """Record a failure. Returns True when the task will be retried.

    A task with attempts left returns to the queue; one that has exhausted them
    is marked failed so the batch can settle and the user sees a row they can
    retry by hand rather than a job that never finishes.
    """
    task.error = error[:2000]
    task.lease_until = None

    will_retry = task.attempts < max_attempts
    if will_retry:
        task.status = TaskStatus.QUEUED
    else:
        task.status = TaskStatus.FAILED
        task.finished_at = _now()

    await session.flush()
    if not will_retry:
        await refresh_job_counters(session, task.job_id)
    return will_retry


async def refresh_job_counters(session: AsyncSession, job_id: UUID) -> None:
    """Recompute a job's counters and status from its tasks.

    Recounting rather than incrementing keeps the numbers correct after a
    retry or a crash, where an increment would drift. It is one indexed
    aggregate over a batch of at most fifty rows.
    """
    rows = (
        await session.execute(
            select(Task.status, func.count()).where(Task.job_id == job_id).group_by(Task.status)
        )
    ).all()
    counts: dict[TaskStatus, int] = {row[0]: row[1] for row in rows}
    done = counts.get(TaskStatus.DONE, 0)
    failed = counts.get(TaskStatus.FAILED, 0)
    total = sum(counts.values())

    if done + failed >= total:
        # A batch where every card failed is a failed job; any success at all
        # still produced usable leads, so the job counts as completed.
        status = JobStatus.FAILED if done == 0 and failed > 0 else JobStatus.COMPLETED
    elif done or failed or counts.get(TaskStatus.PROCESSING):
        status = JobStatus.PROCESSING
    else:
        status = JobStatus.QUEUED

    await session.execute(
        update(Job).where(Job.id == job_id).values(done=done, failed=failed, status=status)
    )


async def release_task(session: AsyncSession, task: Task) -> None:
    """Return a claimed task to the queue without consuming a further attempt.

    Used on graceful shutdown: the card was never processed, so it should be
    picked up promptly rather than waiting for its lease to lapse.
    """
    task.status = TaskStatus.QUEUED
    task.lease_until = None
    await session.flush()


async def queue_depth(session: AsyncSession) -> int:
    """Cards waiting or in flight, used for the queue-depth hint in the UI."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.status.in_((TaskStatus.QUEUED, TaskStatus.PROCESSING)))
        )
    ) or 0
