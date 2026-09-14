"""Stored card image, deduplicated by content hash."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.task import Task


class Image(Base, UUIDPrimaryKey, TimestampMixin):
    """A preprocessed card image on disk (or in S3).

    Rows are keyed by the SHA-256 of the *original* upload, so re-uploading
    the same card reuses the stored file and its previous extraction instead
    of paying for inference again.
    """

    __tablename__ = "images"

    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Dimensions and size after preprocessing (downscale, EXIF orient, strip).
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    tasks: Mapped[list[Task]] = relationship(back_populates="image")

    def __repr__(self) -> str:
        return f"<Image {self.id} {self.original_filename!r} {self.width}x{self.height}>"
