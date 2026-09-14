"""Download a batch as Excel or CSV."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import SessionDep
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger
from app.models import Image, Job, Lead, Task
from app.services.export import build_csv, build_workbook, export_filename

log = get_logger(__name__)
router = APIRouter(prefix="/jobs/{job_id}", tags=["export"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _load_export_data(
    session: SessionDep, job_id: UUID
) -> tuple[list[Lead], dict[UUID, str], list[Task]]:
    if await session.scalar(select(Job.id).where(Job.id == job_id)) is None:
        raise NotFoundError("that job")

    leads = list(
        (
            await session.scalars(
                select(Lead).where(Lead.job_id == job_id).order_by(Lead.created_at)
            )
        ).all()
    )
    if not leads:
        # 409 rather than an empty file: a workbook with only headers looks
        # like the extraction produced nothing, when the batch is likely
        # still running.
        raise AppError("this batch has no extracted leads yet", status_code=409)

    filename_rows = (
        await session.execute(
            select(Image.id, Image.original_filename).where(
                Image.id.in_([lead.image_id for lead in leads])
            )
        )
    ).all()
    filenames: dict[UUID, str] = {row[0]: row[1] for row in filename_rows}
    tasks = list((await session.scalars(select(Task).where(Task.job_id == job_id))).all())
    return leads, filenames, tasks


def _download_headers(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@router.get("/export.xlsx", summary="Download leads as Excel")
async def export_xlsx(job_id: UUID, session: SessionDep) -> Response:
    leads, filenames, tasks = await _load_export_data(session, job_id)
    content = build_workbook(leads, job_id=job_id, filenames=filenames, tasks=tasks)
    log.info("export_generated", job_id=str(job_id), format="xlsx", leads=len(leads))
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers=_download_headers(export_filename(job_id, "xlsx")),
    )


@router.get("/export.csv", summary="Download leads as CSV")
async def export_csv(job_id: UUID, session: SessionDep) -> Response:
    leads, filenames, _ = await _load_export_data(session, job_id)
    content = build_csv(leads, filenames=filenames)
    log.info("export_generated", job_id=str(job_id), format="csv", leads=len(leads))
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers=_download_headers(export_filename(job_id, "csv")),
    )
