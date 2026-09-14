"""Ingest an uploaded file into a card image fit to send to a model.

Uploads are the system's only untrusted input, so validation here is by
decoding rather than by trusting what the client claims. The pipeline is:

    verify it is really an image -> hash the original for deduplication ->
    apply EXIF rotation -> drop all metadata -> downscale -> re-encode JPEG

Metadata removal is a privacy measure as much as a size one: a photo of a
business card taken on a phone carries GPS coordinates of wherever it was
taken, and nothing downstream needs them.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# Imported from the submodule rather than the package root: pillow_heif
# declares no __all__, so the re-export is not visible to type checkers.
from pillow_heif.as_plugin import register_heif_opener

# iPhones produce HEIC by default, which is the single most likely format for a
# photographed card.
register_heif_opener()

# Formats we will decode. A file claiming any other type is rejected before
# Pillow is asked to open it in earnest.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "HEIC", "MPO"}
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

# A 40 megapixel cap: far above any real card photo, far below a decompression
# bomb. Pillow's own limit is left in place as a second guard.
MAX_PIXELS = 40_000_000

JPEG_QUALITY = 85


class ImageValidationError(ValueError):
    """The upload is not an image we can process, with a reason for the user."""


@dataclass(slots=True)
class ProcessedImage:
    """A card image ready to store and send to a model."""

    data: bytes
    sha256: str
    width: int
    height: int
    content_type: str = "image/jpeg"

    @property
    def storage_key(self) -> str:
        # Fanned out by hash prefix so no directory grows unbounded.
        return f"cards/{self.sha256[:2]}/{self.sha256}.jpg"

    def to_data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.data).decode()


def _verify_decodable(raw: bytes) -> str:
    """Confirm the bytes really are a supported image and return the format."""
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = (probe.format or "").upper()
            width, height = probe.size
            # verify() detects truncation and corruption, but leaves the file
            # object unusable, so the real decode happens on a fresh handle.
            probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageValidationError("the file is not a readable image") from exc

    if fmt not in ALLOWED_FORMATS:
        raise ImageValidationError(f"unsupported image format: {fmt or 'unknown'}")
    if width * height > MAX_PIXELS:
        raise ImageValidationError(f"image is too large to process ({width}x{height} pixels)")
    return fmt


def process_upload(raw: bytes, *, max_edge: int, max_bytes: int | None = None) -> ProcessedImage:
    """Validate and normalise one uploaded file.

    The hash is taken over the original bytes, not the processed output, so
    deduplication survives a change to the preprocessing parameters.
    """
    if not raw:
        raise ImageValidationError("the file is empty")
    if max_bytes is not None and len(raw) > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        raise ImageValidationError(f"the file is larger than {limit_mb:.0f} MB")

    _verify_decodable(raw)
    digest = hashlib.sha256(raw).hexdigest()

    try:
        with Image.open(io.BytesIO(raw)) as img:
            # Rotate to the orientation the photographer saw, then convert to
            # RGB, which also discards any alpha channel and colour profile.
            oriented = ImageOps.exif_transpose(img)
            rgb = oriented.convert("RGB")
            rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            # A fresh save with no exif argument is what strips metadata.
            rgb.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
            width, height = rgb.width, rgb.height
    except (OSError, ValueError) as exc:
        raise ImageValidationError("the image could not be processed") from exc

    return ProcessedImage(data=buffer.getvalue(), sha256=digest, width=width, height=height)


async def process_upload_async(
    raw: bytes, *, max_edge: int, max_bytes: int | None = None
) -> ProcessedImage:
    """Run `process_upload` off the event loop.

    Decoding a large photo is CPU-bound and would otherwise stall every other
    request while a batch is being uploaded.
    """
    return await asyncio.to_thread(process_upload, raw, max_edge=max_edge, max_bytes=max_bytes)


def make_thumbnail(raw: bytes, *, max_edge: int = 240) -> bytes:
    """A small preview for the results table."""
    with Image.open(io.BytesIO(raw)) as img:
        rgb = ImageOps.exif_transpose(img).convert("RGB")
        rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, "JPEG", quality=80, optimize=True)
    return buffer.getvalue()
