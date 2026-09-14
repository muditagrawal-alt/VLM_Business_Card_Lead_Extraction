"""Data model behaviour."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Image, Job, JobStatus, Lead, Task, TaskStatus


def _image(sha: str = "a" * 64) -> Image:
    return Image(
        sha256=sha,
        storage_key=f"images/{sha}.jpg",
        original_filename="card.jpg",
        content_type="image/jpeg",
        width=768,
        height=432,
        bytes=91_234,
    )


class TestJobCounters:
    def test_counters_are_safe_before_flush(self) -> None:
        """A Job built in memory has no defaults applied yet.

        Column defaults are applied at INSERT, so total/done/failed are None
        until the row is flushed. The status endpoint reads these properties on
        a freshly created job, so they must not raise.
        """
        job = Job(total=5)
        assert job.pending == 5
        assert job.is_settled is False

    def test_counters_on_empty_job(self) -> None:
        job = Job()
        assert job.pending == 0
        # A job with no cards is trivially settled rather than stuck.
        assert job.is_settled is True

    @pytest.mark.parametrize(
        ("total", "done", "failed", "pending", "settled"),
        [
            (10, 0, 0, 10, False),
            (10, 4, 1, 5, False),
            (10, 8, 2, 0, True),
            (10, 0, 10, 0, True),
            # Defensive: counters must never yield a negative pending count.
            (2, 3, 1, 0, True),
        ],
    )
    def test_pending_and_settled(
        self, total: int, done: int, failed: int, pending: int, settled: bool
    ) -> None:
        job = Job(total=total, done=done, failed=failed)
        assert job.pending == pending
        assert job.is_settled is settled

    @pytest.mark.asyncio
    async def test_defaults_applied_on_insert(self, session) -> None:
        job = Job()
        session.add(job)
        await session.flush()
        assert (job.total, job.done, job.failed) == (0, 0, 0)
        assert job.status is JobStatus.QUEUED
        assert job.created_at is not None


class TestLead:
    def test_full_name_joins_parts(self) -> None:
        assert Lead(first_name="Ada", last_name="Lovelace").full_name == "Ada Lovelace"

    @pytest.mark.parametrize(
        ("first", "last", "expected"),
        [
            ("Ada", None, "Ada"),
            (None, "Lovelace", "Lovelace"),
            (None, None, None),
        ],
    )
    def test_full_name_with_missing_parts(
        self, first: str | None, last: str | None, expected: str | None
    ) -> None:
        """A card may print only one name; that must not produce stray spaces."""
        assert Lead(first_name=first, last_name=last).full_name == expected

    @pytest.mark.asyncio
    async def test_every_extracted_field_is_optional(self, session) -> None:
        """A card with no readable contact details must still persist.

        Nullability is deliberate: an invented value is worse than a blank one.
        """
        job = Job(total=1)
        image = _image()
        session.add_all([job, image])
        await session.flush()

        task = Task(job_id=job.id, image_id=image.id, status=TaskStatus.DONE)
        session.add(task)
        await session.flush()

        lead = Lead(job_id=job.id, task_id=task.id, image_id=image.id)
        session.add(lead)
        await session.flush()

        assert lead.id is not None
        assert lead.full_name is None
        assert lead.edited_by_user is False


class TestImageDeduplication:
    @pytest.mark.asyncio
    async def test_same_content_hash_is_rejected(self, session) -> None:
        """The unique hash is what lets a re-upload skip paying for inference."""
        session.add(_image("b" * 64))
        await session.flush()

        session.add(_image("b" * 64))
        with pytest.raises(IntegrityError):
            await session.flush()


class TestCascades:
    @pytest.mark.asyncio
    async def test_deleting_a_job_removes_its_tasks_and_leads(self, session) -> None:
        """Job deletion backs both the user purge and the retention sweep."""
        job = Job(total=1)
        image = _image("c" * 64)
        session.add_all([job, image])
        await session.flush()

        task = Task(job_id=job.id, image_id=image.id)
        session.add(task)
        await session.flush()
        session.add(Lead(job_id=job.id, task_id=task.id, image_id=image.id))
        await session.flush()

        await session.delete(job)
        await session.flush()

        assert (await session.scalars(select(Task))).all() == []
        assert (await session.scalars(select(Lead))).all() == []
        # The image is content-addressed and shared between jobs, so it stays.
        assert len((await session.scalars(select(Image))).all()) == 1


class TestTaskQueueFields:
    @pytest.mark.asyncio
    async def test_new_task_is_claimable(self, session) -> None:
        job = Job(total=1)
        image = _image("d" * 64)
        session.add_all([job, image])
        await session.flush()

        task = Task(job_id=job.id, image_id=image.id)
        session.add(task)
        await session.flush()

        assert task.status is TaskStatus.QUEUED
        assert task.attempts == 0
        assert task.lease_until is None
        assert task.provider is None
        assert isinstance(task.id, uuid.UUID)
