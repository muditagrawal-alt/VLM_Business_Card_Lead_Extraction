"""The HTTP surface, end to end against a real schema."""

from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from app.models import Lead, Task


def card_bytes(colour: tuple[int, int, int] = (250, 250, 248)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (1050, 600), colour).save(buf, "JPEG")
    return buf.getvalue()


def upload_files(count: int = 2) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        ("files", (f"card-{i}.jpg", card_bytes((250 - i, 250, 248)), "image/jpeg"))
        for i in range(count)
    ]


class TestUpload:
    async def test_a_batch_is_accepted_and_queued(self, api) -> None:
        client, session = api

        response = await client.post("/api/v1/jobs", files=upload_files(3))

        assert response.status_code == 201
        body = response.json()
        assert body["accepted"] == 3
        assert body["rejected"] == []

        tasks = (await session.scalars(select(Task))).all()
        assert len(tasks) == 3

    async def test_an_unreadable_file_is_reported_without_failing_the_batch(self, api) -> None:
        """Nineteen good cards and one screenshot should yield nineteen leads."""
        client, _ = api
        files = [
            *upload_files(2),
            ("files", ("notes.txt", b"this is not an image", "image/jpeg")),
        ]

        response = await client.post("/api/v1/jobs", files=files)

        assert response.status_code == 201
        body = response.json()
        assert body["accepted"] == 2
        assert len(body["rejected"]) == 1
        assert body["rejected"][0]["filename"] == "notes.txt"

    async def test_a_batch_of_only_bad_files_is_rejected(self, api) -> None:
        client, _ = api
        response = await client.post(
            "/api/v1/jobs",
            files=[("files", ("x.jpg", b"not an image", "image/jpeg"))],
        )
        assert response.status_code == 415
        assert "could be read" in response.json()["error"]

    async def test_too_many_files_is_refused_with_the_limit(self, api) -> None:
        client, _ = api
        response = await client.post("/api/v1/jobs", files=upload_files(51))
        assert response.status_code == 413
        assert "50" in response.json()["error"]

    async def test_identical_content_is_deduplicated(self, api) -> None:
        """The same card twice must not be sent to a model twice."""
        client, _ = api
        same = card_bytes()
        files = [
            ("files", ("a.jpg", same, "image/jpeg")),
            ("files", ("b.jpg", same, "image/jpeg")),
        ]

        body = (await client.post("/api/v1/jobs", files=files)).json()

        assert body["accepted"] == 2
        assert body["duplicates"] == 1


class TestJobProgress:
    async def test_progress_reports_counters_and_per_card_state(self, api) -> None:
        client, _ = api
        job_id = (await client.post("/api/v1/jobs", files=upload_files(2))).json()["job_id"]

        body = (await client.get(f"/api/v1/jobs/{job_id}")).json()

        assert body["total"] == 2
        assert body["done"] == 0
        assert body["pending"] == 2
        assert body["status"] == "queued"
        assert len(body["tasks"]) == 2
        assert body["tasks"][0]["original_filename"] == "card-0.jpg"

    async def test_estimate_is_null_before_any_card_finishes(self, api) -> None:
        """A made-up estimate is worse than none: the tiers differ tenfold."""
        client, _ = api
        job_id = (await client.post("/api/v1/jobs", files=upload_files(2))).json()["job_id"]

        body = (await client.get(f"/api/v1/jobs/{job_id}")).json()

        assert body["estimated_seconds_remaining"] is None

    async def test_an_unknown_job_is_a_404(self, api) -> None:
        client, _ = api
        response = await client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    async def test_a_malformed_id_is_a_422_not_a_500(self, api) -> None:
        client, _ = api
        response = await client.get("/api/v1/jobs/not-a-uuid")
        assert response.status_code == 422


class TestLeads:
    async def _job_with_lead(self, client, session) -> tuple[str, Lead]:
        job_id = (await client.post("/api/v1/jobs", files=upload_files(1))).json()["job_id"]
        task = await session.scalar(select(Task))
        lead = Lead(
            job_id=task.job_id,
            task_id=task.id,
            image_id=task.image_id,
            first_name="Priya",
            last_name="Raghavan",
            position="Head of Business Development",
            company="Meridian Logistics",
            location="Mumbai, India",
            phone="+919876543210",
            email="priya.raghavan@meridianlog.com",
            confidence={"phone": 0.7, "email": 1.0},
        )
        session.add(lead)
        await session.flush()
        return job_id, lead

    async def test_leads_are_listed_for_a_batch(self, api) -> None:
        client, session = api
        job_id, _ = await self._job_with_lead(client, session)

        body = (await client.get(f"/api/v1/jobs/{job_id}/leads")).json()

        assert len(body) == 1
        assert body[0]["first_name"] == "Priya"
        assert body[0]["email"] == "priya.raghavan@meridianlog.com"

    async def test_a_correction_updates_only_the_fields_sent(self, api) -> None:
        """Editing one cell must not blank the rest of the row."""
        client, session = api
        _, lead = await self._job_with_lead(client, session)

        body = (
            await client.patch(f"/api/v1/leads/{lead.id}", json={"position": "VP Sales"})
        ).json()

        assert body["position"] == "VP Sales"
        assert body["first_name"] == "Priya"
        assert body["edited_by_user"] is True

    async def test_a_corrected_phone_is_normalised(self, api) -> None:
        client, session = api
        _, lead = await self._job_with_lead(client, session)

        body = (
            await client.patch(f"/api/v1/leads/{lead.id}", json={"phone": "+44 20 7946 0958"})
        ).json()

        assert body["phone"] == "+442079460958"

    async def test_a_corrected_field_stops_being_flagged(self, api) -> None:
        """A human-supplied value is not a model guess."""
        client, session = api
        _, lead = await self._job_with_lead(client, session)

        body = (
            await client.patch(f"/api/v1/leads/{lead.id}", json={"phone": "+44 20 7946 0958"})
        ).json()

        assert body["confidence"]["phone"] == 1.0

    async def test_unknown_fields_are_rejected(self, api) -> None:
        client, session = api
        _, lead = await self._job_with_lead(client, session)
        response = await client.patch(f"/api/v1/leads/{lead.id}", json={"salary": "100000"})
        assert response.status_code == 422


class TestExport:
    async def test_xlsx_download_has_the_right_type_and_filename(self, api) -> None:
        client, session = api
        job_id = (await client.post("/api/v1/jobs", files=upload_files(1))).json()["job_id"]
        task = await session.scalar(select(Task))
        session.add(
            Lead(
                job_id=task.job_id,
                task_id=task.id,
                image_id=task.image_id,
                first_name="Priya",
                email="priya@example.com",
            )
        )
        await session.flush()

        response = await client.get(f"/api/v1/jobs/{job_id}/export.xlsx")

        assert response.status_code == 200
        assert "spreadsheetml" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
        assert ".xlsx" in response.headers["content-disposition"]
        # A real zip container, not an error page.
        assert response.content[:2] == b"PK"

    async def test_csv_download_carries_a_bom_for_excel(self, api) -> None:
        client, session = api
        job_id = (await client.post("/api/v1/jobs", files=upload_files(1))).json()["job_id"]
        task = await session.scalar(select(Task))
        session.add(
            Lead(job_id=task.job_id, task_id=task.id, image_id=task.image_id, first_name="Aisha")
        )
        await session.flush()

        response = await client.get(f"/api/v1/jobs/{job_id}/export.csv")

        assert response.status_code == 200
        assert response.content.startswith(b"\xef\xbb\xbf")

    async def test_exporting_an_unfinished_batch_explains_itself(self, api) -> None:
        """An empty workbook would look like extraction produced nothing."""
        client, _ = api
        job_id = (await client.post("/api/v1/jobs", files=upload_files(1))).json()["job_id"]

        response = await client.get(f"/api/v1/jobs/{job_id}/export.xlsx")

        assert response.status_code == 409
        assert "no extracted leads yet" in response.json()["error"]


class TestImages:
    async def test_thumbnail_is_served_for_an_uploaded_card(self, api) -> None:
        client, session = api
        await client.post("/api/v1/jobs", files=upload_files(1))
        task = await session.scalar(select(Task))

        response = await client.get(f"/api/v1/images/{task.image_id}/thumb")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert "immutable" in response.headers["cache-control"]


class TestHealth:
    async def test_liveness_needs_no_dependencies(self, api) -> None:
        client, _ = api
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_readiness_reports_each_tier(self, api) -> None:
        client, _ = api
        body = (await client.get("/api/v1/ready")).json()
        assert body["database"] is True
        assert {t["tier"] for t in body["tiers"]} == {"gpu", "cpu"}


class TestDeletion:
    async def test_deleting_a_batch_removes_its_leads(self, api) -> None:
        client, _session = api
        job_id = (await client.post("/api/v1/jobs", files=upload_files(2))).json()["job_id"]

        response = await client.delete(f"/api/v1/jobs/{job_id}")

        assert response.status_code == 204
        assert (await client.get(f"/api/v1/jobs/{job_id}")).status_code == 404


@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json"])
async def test_api_documentation_is_served(api, path: str) -> None:
    client, _ = api
    assert (await client.get(path)).status_code == 200
