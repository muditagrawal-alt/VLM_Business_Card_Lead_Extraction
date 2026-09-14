"""Serve card images and thumbnails for the review table."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import SessionDep, StorageDep
from app.core.errors import NotFoundError
from app.models import Image
from app.services.images import make_thumbnail

router = APIRouter(prefix="/images", tags=["images"])

# Images are immutable and content-addressed, so they can be cached hard.
_CACHE_CONTROL = "private, max-age=86400, immutable"


async def _load_bytes(session: SessionDep, storage: StorageDep, image_id: UUID) -> bytes:
    image = await session.scalar(select(Image).where(Image.id == image_id))
    if image is None:
        raise NotFoundError("that image")
    try:
        return await storage.read(image.storage_key)
    except OSError as exc:
        raise NotFoundError("that image file") from exc


@router.get("/{image_id}", summary="Full card image")
async def get_image(image_id: UUID, session: SessionDep, storage: StorageDep) -> Response:
    data = await _load_bytes(session, storage, image_id)
    return Response(
        content=data, media_type="image/jpeg", headers={"Cache-Control": _CACHE_CONTROL}
    )


@router.get("/{image_id}/thumb", summary="Card thumbnail")
async def get_thumbnail(image_id: UUID, session: SessionDep, storage: StorageDep) -> Response:
    data = await _load_bytes(session, storage, image_id)
    return Response(
        content=make_thumbnail(data),
        media_type="image/jpeg",
        headers={"Cache-Control": _CACHE_CONTROL},
    )
