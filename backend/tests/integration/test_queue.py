"""Durable queue semantics: claiming, crash recovery, retries and counters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Image, Job, JobStatus, Task, TaskStatus
from app.services import queue

LEASE = 300
MAX_ATTEMPTS = 2


async def seed(session, count: int = 3) -> Job:
    """A job with `count` queued tasks, each with its own image."""
    job = Job(total=count)
    session.add(job)
    await session.flush()

    for index in range(count):
        digest = f"{index:064x}"
        image = Image(
            sha256=digest,
            storage_key=f"cards/{digest[:2]}/{digest}.jpg",
            original_filename=f"card-{index}.jpg",
            content_type="image/jpeg",
            width=768,
            height=439,
            bytes=20_000,
        )
        session.add(image)
        await session.flush()
        session.add(Task(job_id=job.id, image_id=image.id))
    await session.flush()
    return job


class TestClaiming:
    async def test_claim_marks_tasks_in_progress_with_a_lease(self, session) -> None:
        await seed(session, 3)

        claimed = await queue.claim_tasks(
            session, limit=2, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )

        assert len(claimed) == 2
        for task in claimed:
            assert task.status is TaskStatus.PROCESSING
            assert task.attempts == 1
            assert task.lease_until is not None

    async def test_claim_respects_the_limit(self, session) -> None:
        await seed(session, 5)
        claimed = await queue.claim_tasks(
            session, limit=2, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        assert len(claimed) == 2

    async def test_already_claimed_tasks_are_not_claimed_again(self, session) -> None:
        await seed(session, 2)
        first = await queue.claim_tasks(
            session, limit=2, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        second = await queue.claim_tasks(
            session, limit=2, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        assert len(first) == 2
        assert second == []

    async def test_empty_queue_returns_nothing(self, session) -> None:
        assert (
            await queue.claim_tasks(
                session, limit=4, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
            )
            == []
        )

    async def test_oldest_task_is_claimed_first(self, session) -> None:
        """Batches should complete in upload order, not arbitrarily."""
        await seed(session, 3)
        all_tasks = list((await session.scalars(select(Task).order_by(Task.created_at))).all())

        claimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        assert claimed[0].id == all_tasks[0].id


class TestCrashRecovery:
    async def test_an_expired_lease_makes_a_task_claimable_again(self, session) -> None:
        """This is what makes a worker crash cost one retry, not a lost card.

        A worker that dies mid-card never reports anything, so the only signal
        is its lease lapsing.
        """
        await seed(session, 1)
        claimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        # Simulate the worker dying: still PROCESSING, but the lease has passed.
        claimed[0].lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()

        reclaimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )

        assert len(reclaimed) == 1
        assert reclaimed[0].id == claimed[0].id
        # The dead worker's attempt still counted, so a card that reliably
        # kills its worker cannot be retried forever.
        assert reclaimed[0].attempts == 2

    async def test_a_task_that_exhausted_its_attempts_is_not_reclaimed(self, session) -> None:
        await seed(session, 1)
        for _ in range(MAX_ATTEMPTS):
            claimed = await queue.claim_tasks(
                session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
            )
            claimed[0].lease_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.flush()

        assert (
            await queue.claim_tasks(
                session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
            )
            == []
        )

    async def test_heartbeat_extends_a_lease(self, session) -> None:
        """A card slower than its lease must keep its claim.

        Otherwise another worker picks up work already in flight and the card
        is paid for twice.
        """
        await seed(session, 1)
        claimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=10, max_attempts=MAX_ATTEMPTS
        )
        original = claimed[0].lease_until
        assert original is not None

        await queue.heartbeat(session, claimed[0].id, lease_seconds=600)
        await session.refresh(claimed[0])

        assert claimed[0].lease_until is not None
        assert claimed[0].lease_until > original

    async def test_release_returns_a_task_without_consuming_an_attempt(self, session) -> None:
        """Graceful shutdown should not charge the card an attempt."""
        await seed(session, 1)
        claimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        attempts_before = claimed[0].attempts

        await queue.release_task(session, claimed[0])

        assert claimed[0].status is TaskStatus.QUEUED
        assert claimed[0].lease_until is None
        assert claimed[0].attempts == attempts_before


class TestSettling:
    async def test_a_failure_with_attempts_left_is_requeued(self, session) -> None:
        await seed(session, 1)
        claimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )

        retrying = await queue.mark_failed(
            session, claimed[0], error="tier timeout", max_attempts=MAX_ATTEMPTS
        )

        assert retrying is True
        assert claimed[0].status is TaskStatus.QUEUED
        assert claimed[0].error == "tier timeout"

    async def test_a_failure_on_the_last_attempt_is_final(self, session) -> None:
        """The batch must be able to settle even when a card cannot be read."""
        job = await seed(session, 1)
        for _ in range(MAX_ATTEMPTS):
            claimed = await queue.claim_tasks(
                session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
            )
            retrying = await queue.mark_failed(
                session, claimed[0], error="unreadable", max_attempts=MAX_ATTEMPTS
            )

        assert retrying is False
        assert claimed[0].status is TaskStatus.FAILED
        assert claimed[0].finished_at is not None
        await session.refresh(job)
        assert job.failed == 1

    async def test_a_long_error_is_truncated_rather_than_rejected(self, session) -> None:
        await seed(session, 1)
        claimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        await queue.mark_failed(session, claimed[0], error="x" * 5000, max_attempts=MAX_ATTEMPTS)
        assert claimed[0].error is not None
        assert len(claimed[0].error) == 2000


class TestJobCounters:
    async def test_counters_track_completion(self, session) -> None:
        job = await seed(session, 3)
        claimed = await queue.claim_tasks(
            session, limit=3, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )

        await queue.mark_done(session, claimed[0])
        await session.refresh(job)
        assert (job.done, job.failed, job.status) == (1, 0, JobStatus.PROCESSING)

        await queue.mark_done(session, claimed[1])
        await queue.mark_done(session, claimed[2])
        await session.refresh(job)
        assert (job.done, job.failed, job.status) == (3, 0, JobStatus.COMPLETED)

    async def test_a_partly_failed_batch_still_completes(self, session) -> None:
        """Usable leads were produced, so the job is not a failure."""
        job = await seed(session, 2)
        claimed = await queue.claim_tasks(session, limit=2, lease_seconds=LEASE, max_attempts=1)
        await queue.mark_done(session, claimed[0])
        await queue.mark_failed(session, claimed[1], error="unreadable", max_attempts=1)

        await session.refresh(job)
        assert (job.done, job.failed) == (1, 1)
        assert job.status is JobStatus.COMPLETED

    async def test_a_wholly_failed_batch_is_marked_failed(self, session) -> None:
        job = await seed(session, 2)
        claimed = await queue.claim_tasks(session, limit=2, lease_seconds=LEASE, max_attempts=1)
        for task in claimed:
            await queue.mark_failed(session, task, error="tier down", max_attempts=1)

        await session.refresh(job)
        assert job.status is JobStatus.FAILED

    async def test_counters_are_recomputed_not_incremented(self, session) -> None:
        """Recounting is what keeps the numbers right after a retry.

        An increment would double-count a card that failed once and then
        succeeded.
        """
        job = await seed(session, 1)
        claimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        await queue.mark_failed(session, claimed[0], error="transient", max_attempts=MAX_ATTEMPTS)
        reclaimed = await queue.claim_tasks(
            session, limit=1, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        await queue.mark_done(session, reclaimed[0])

        await session.refresh(job)
        assert (job.done, job.failed) == (1, 0)
        assert job.status is JobStatus.COMPLETED


class TestQueueDepth:
    async def test_depth_counts_waiting_and_in_flight_cards(self, session) -> None:
        await seed(session, 4)
        assert await queue.queue_depth(session) == 4

        claimed = await queue.claim_tasks(
            session, limit=2, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
        )
        # Still four: two waiting, two in flight.
        assert await queue.queue_depth(session) == 4

        await queue.mark_done(session, claimed[0])
        assert await queue.queue_depth(session) == 3


@pytest.mark.parametrize("count", [1, 5, 50])
async def test_claiming_scales_to_a_full_batch(session, count: int) -> None:
    await seed(session, count)
    claimed = await queue.claim_tasks(
        session, limit=count, lease_seconds=LEASE, max_attempts=MAX_ATTEMPTS
    )
    assert len(claimed) == count
