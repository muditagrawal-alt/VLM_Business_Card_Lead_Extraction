"""Upload a batch of cards and follow its progress."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, File, Request, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import AccessCodeDep, SessionDep, SettingsDep, StorageDep
from app.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.core.rate_limit import client_ip_hash, limiter
from app.models import Image, Job, Task, TaskStatus
from app.schemas.api import JobCreated, JobDetail, JobOut, TaskOut, UploadRejection
from app.services import queue
from app.services.images import ImageValidationError, process_upload_async

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Upload business cards",
)
# The limit comes from settings through a callable so it is configurable per
# deployment, and can be switched off in tests without patching the decorator.
@limiter.limit(lambda: get_settings().rate_limit_jobs)
async def create_job(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    _: AccessCodeDep,
    files: list[UploadFile] = File(..., description="Business card images"),
) -> JobCreated:
    """Accept a batch of images and queue one extraction task per card.

    Invalid files are reported individually rather than failing the batch: a
    user who drags in twenty cards and one screenshot should get nineteen
    leads and one clear message, not an error page.
    """
    if not files:
        raise AppError("no files were uploaded")
    if len(files) > settings.max_files_per_job:
        raise AppError(
            f"at most {settings.max_files_per_job} files can be uploaded at once "
            f"({len(files)} were sent)",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    job = Job(
        client_ip_hash=client_ip_hash(request),
        expires_at=datetime.now(UTC) + timedelta(days=settings.retention_days),
    )
    session.add(job)
    await session.flush()

    rejected: list[UploadRejection] = []
    duplicates = 0
    accepted = 0

    for upload in files:
        filename = upload.filename or "unnamed"
        try:
            raw = await upload.read()
            processed = await process_upload_async(
                raw,
                max_edge=settings.image_max_edge_px,
                max_bytes=settings.max_file_size_bytes,
            )
        except ImageValidationError as exc:
            rejected.append(UploadRejection(filename=filename, reason=str(exc)))
            continue
        finally:
            await upload.close()

        existing = await session.scalar(select(Image).where(Image.sha256 == processed.sha256))
        if existing is None:
            image = Image(
                sha256=processed.sha256,
                storage_key=processed.storage_key,
                original_filename=filename,
                content_type=processed.content_type,
                width=processed.width,
                height=processed.height,
                bytes=len(processed.data),
            )
            session.add(image)
            await session.flush()
            await storage.write(processed.storage_key, processed.data)
        else:
            # Same bytes as a previous upload: reuse the stored file rather
            # than writing it twice.
            image = existing
            duplicates += 1

        session.add(Task(job_id=job.id, image_id=image.id))
        accepted += 1

    if accepted == 0:
        raise AppError(
            "none of the uploaded files could be read as an image",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    job.total = accepted
    await session.flush()

    log.info(
        "job_created",
        job_id=str(job.id),
        accepted=accepted,
        rejected=len(rejected),
        duplicates=duplicates,
    )
    return JobCreated(job_id=job.id, accepted=accepted, duplicates=duplicates, rejected=rejected)


async def _load_job(session: AsyncSession, job_id: UUID) -> Job:
    job = await session.scalar(select(Job).where(Job.id == job_id))
    if job is None:
        raise NotFoundError("that job")
    return job


@router.get("/{job_id}", response_model=JobDetail, summary="Batch progress")
async def get_job(job_id: UUID, session: SessionDep) -> JobDetail:
    job = await _load_job(session, job_id)

    rows = (
        await session.execute(
            select(Task, Image.original_filename)
            .join(Image, Image.id == Task.image_id)
            .where(Task.job_id == job_id)
            .order_by(Task.created_at)
        )
    ).all()

    tasks = [
        TaskOut.model_validate(task).model_copy(update={"original_filename": filename})
        for task, filename in rows
    ]

    return JobDetail(
        id=job.id,
        status=job.status,
        total=job.total,
        done=job.done,
        failed=job.failed,
        pending=job.pending,
        created_at=job.created_at,
        updated_at=job.updated_at,
        tasks=tasks,
        queue_depth=await queue.queue_depth(session),
        estimated_seconds_remaining=await _estimate_remaining(session, job),
    )


async def _estimate_remaining(session: AsyncSession, job: Job) -> int | None:
    """Estimate from this batch's own measured latency.

    Returns null until a card has finished. Any figure before that would be
    invented, and a made-up estimate is worse than none: the tiers differ by
    an order of magnitude, so a guess could be wrong by minutes.
    """
    if job.pending == 0:
        return 0

    mean_ms = await session.scalar(
        select(func.avg(Task.latency_ms)).where(Task.job_id == job.id, Task.latency_ms.is_not(None))
    )
    if mean_ms is None:
        return None
    return int(job.pending * float(mean_ms) / 1000)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a batch")
async def delete_job(job_id: UUID, session: SessionDep) -> None:
    """Delete a batch and everything extracted from it.

    Images are content-addressed and may be shared with another batch, so the
    files themselves are left to the retention sweep.
    """
    job = await _load_job(session, job_id)
    await session.delete(job)
    log.info("job_deleted", job_id=str(job_id))


@router.get("", response_model=list[JobOut], summary="Recent batches")
async def list_jobs(session: SessionDep, limit: int = 20) -> list[JobOut]:
    jobs = (
        await session.scalars(select(Job).order_by(Job.created_at.desc()).limit(min(limit, 100)))
    ).all()
    return [JobOut.model_validate(job) for job in jobs]


@router.post(
    "/{job_id}/tasks/{task_id}/retry",
    response_model=TaskOut,
    summary="Retry a failed card",
)
async def retry_task(job_id: UUID, task_id: UUID, session: SessionDep) -> TaskOut:
    """Requeue a single failed card.

    The attempt counter is reset because this is a deliberate human decision,
    not the automatic retry the counter exists to bound.
    """
    task = await session.scalar(select(Task).where(Task.id == task_id, Task.job_id == job_id))
    if task is None:
        raise NotFoundError("that card")
    if task.status is not TaskStatus.FAILED:
        raise AppError("only a failed card can be retried")

    task.status = TaskStatus.QUEUED
    task.attempts = 0
    task.error = None
    task.finished_at = None
    await session.flush()
    await queue.refresh_job_counters(session, job_id)

    log.info("task_retry_requested", task_id=str(task_id))
    return TaskOut.model_validate(task)
