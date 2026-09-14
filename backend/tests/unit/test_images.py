"""Upload ingestion — the system's only untrusted input."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.images import (
    ImageValidationError,
    make_thumbnail,
    process_upload,
)


def make_image(
    width: int = 1050,
    height: int = 600,
    fmt: str = "JPEG",
    colour: tuple[int, int, int] = (200, 40, 40),
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, fmt)
    return buf.getvalue()


class TestRejection:
    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("empty file", b""),
            ("plain text", b"this is not an image" * 50),
            ("truncated png header", bytes.fromhex("89504e470d0a1a0a")),
            ("zip archive", b"PK\x03\x04" + b"\x00" * 200),
        ],
    )
    def test_non_images_are_rejected(self, label: str, payload: bytes) -> None:
        with pytest.raises(ImageValidationError):
            process_upload(payload, max_edge=768)

    def test_a_renamed_file_cannot_smuggle_past_validation(self) -> None:
        """Validation decodes the bytes rather than trusting the filename.

        A .jpg extension on a text file is the cheapest possible attack.
        """
        with pytest.raises(ImageValidationError, match="not a readable image"):
            process_upload(b"GIF89a-but-not-really", max_edge=768)

    def test_oversized_files_are_rejected_with_the_limit(self) -> None:
        payload = make_image(2000, 2000)
        with pytest.raises(ImageValidationError, match="larger than"):
            process_upload(payload, max_edge=768, max_bytes=1024)

    def test_truncated_image_data_is_rejected(self) -> None:
        """A cut-off upload must fail loudly, not yield a half-read card."""
        whole = make_image()
        with pytest.raises(ImageValidationError):
            process_upload(whole[: len(whole) // 3], max_edge=768)


class TestProcessing:
    def test_large_image_is_downscaled_within_the_edge_limit(self) -> None:
        result = process_upload(make_image(4000, 3000), max_edge=768)
        assert max(result.width, result.height) == 768
        # Aspect ratio preserved: 4000x3000 is 4:3.
        assert result.width / result.height == pytest.approx(4 / 3, abs=0.01)

    def test_small_image_is_not_upscaled(self) -> None:
        result = process_upload(make_image(320, 200), max_edge=768)
        assert (result.width, result.height) == (320, 200)

    def test_output_is_always_jpeg(self) -> None:
        result = process_upload(make_image(fmt="PNG"), max_edge=768)
        assert result.content_type == "image/jpeg"
        with Image.open(io.BytesIO(result.data)) as img:
            assert img.format == "JPEG"

    def test_transparency_is_flattened_rather_than_failing(self) -> None:
        buf = io.BytesIO()
        Image.new("RGBA", (400, 300), (10, 20, 30, 128)).save(buf, "PNG")
        result = process_upload(buf.getvalue(), max_edge=768)
        with Image.open(io.BytesIO(result.data)) as img:
            assert img.mode == "RGB"


class TestMetadataRemoval:
    def test_exif_is_stripped_from_the_stored_image(self) -> None:
        """A phone photo of a card carries GPS coordinates.

        Nothing downstream needs them, so they must not survive ingestion.
        """
        source = Image.new("RGB", (800, 600), (5, 5, 5))
        buf = io.BytesIO()
        exif = source.getexif()
        exif[0x010E] = "ImageDescription set by the camera"
        exif[0x0110] = "TestCamera"
        source.save(buf, "JPEG", exif=exif)
        assert b"TestCamera" in buf.getvalue()

        result = process_upload(buf.getvalue(), max_edge=768)
        assert b"TestCamera" not in result.data
        with Image.open(io.BytesIO(result.data)) as img:
            assert not dict(img.getexif())

    def test_exif_orientation_is_applied_before_stripping(self) -> None:
        """Rotation must be baked in, or a sideways card reaches the model.

        Orientation 6 means "rotate 90 degrees clockwise for display", so a
        landscape image stored with that tag should come out portrait.
        """
        source = Image.new("RGB", (900, 300), (9, 9, 9))
        buf = io.BytesIO()
        exif = source.getexif()
        exif[0x0112] = 6
        source.save(buf, "JPEG", exif=exif)

        result = process_upload(buf.getvalue(), max_edge=1200)
        assert (result.width, result.height) == (300, 900)


class TestDeduplication:
    def test_identical_uploads_share_a_hash(self) -> None:
        payload = make_image()
        assert process_upload(payload, max_edge=768).sha256 == (
            process_upload(payload, max_edge=768).sha256
        )

    def test_hash_is_independent_of_preprocessing_settings(self) -> None:
        """Hashing the original means dedupe survives a tuning change.

        If the hash were taken over the processed output, changing the edge
        limit would silently orphan every previously stored card.
        """
        payload = make_image()
        assert (
            process_upload(payload, max_edge=768).sha256
            == process_upload(payload, max_edge=512).sha256
        )

    def test_different_images_do_not_collide(self) -> None:
        a = process_upload(make_image(colour=(1, 2, 3)), max_edge=768)
        b = process_upload(make_image(colour=(3, 2, 1)), max_edge=768)
        assert a.sha256 != b.sha256

    def test_storage_key_is_fanned_out_by_hash_prefix(self) -> None:
        result = process_upload(make_image(), max_edge=768)
        assert result.storage_key == f"cards/{result.sha256[:2]}/{result.sha256}.jpg"

    def test_data_url_is_usable_by_the_providers(self) -> None:
        url = process_upload(make_image(), max_edge=768).to_data_url()
        assert url.startswith("data:image/jpeg;base64,")


class TestThumbnail:
    def test_thumbnail_is_bounded_and_smaller_than_the_card(self) -> None:
        payload = make_image(2000, 1400)
        thumb = make_thumbnail(payload, max_edge=240)
        with Image.open(io.BytesIO(thumb)) as img:
            assert max(img.size) == 240
        assert len(thumb) < len(payload)
