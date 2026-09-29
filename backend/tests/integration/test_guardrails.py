"""The public URL's guardrails.

There are no accounts, so anything reachable is reachable by anyone who has the
link. These tests pin what a stranger can and cannot do with it: read other
people's leads, spend inference, or destroy data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from app.config import Settings, get_settings
from tests.integration.test_api import upload_files

if TYPE_CHECKING:
    from httpx import AsyncClient


def configure(client: AsyncClient, **overrides: object) -> None:
    """Swap the settings the routes see for this test."""
    settings = Settings(
        app_env="development",
        database_url="sqlite+aiosqlite:///:memory:",
        vlm_cloud_api_key="",
        **overrides,  # type: ignore[arg-type]
    )
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_settings] = lambda: settings


class TestNoBatchListing:
    async def test_there_is_no_endpoint_that_lists_every_batch(self, api) -> None:
        """Such a list, without accounts, is everyone's leads to anyone."""
        client, _ = api
        await client.post("/api/v1/jobs", files=upload_files(1))

        response = await client.get("/api/v1/jobs")

        assert response.status_code == 405

    async def test_an_unknown_batch_id_reveals_nothing(self, api) -> None:
        client, _ = api
        response = await client.get(f"/api/v1/jobs/{uuid4()}/leads")
        assert response.status_code == 404


class TestAccessCode:
    async def test_upload_needs_the_code_when_one_is_set(self, api) -> None:
        client, _ = api
        configure(client, app_access_code="open-sesame")

        refused = await client.post("/api/v1/jobs", files=upload_files(1))
        wrong = await client.post(
            "/api/v1/jobs", files=upload_files(1), headers={"X-Access-Code": "open-sesam"}
        )
        allowed = await client.post(
            "/api/v1/jobs", files=upload_files(1), headers={"X-Access-Code": "open-sesame"}
        )

        assert refused.status_code == 401
        assert "access code" in refused.json()["error"]
        assert wrong.status_code == 401
        assert allowed.status_code == 201

    async def test_delete_and_retry_are_gated_too(self, api) -> None:
        """Otherwise a stranger could empty the database or spend inference."""
        client, _ = api
        created = await client.post("/api/v1/jobs", files=upload_files(1))
        job_id = created.json()["job_id"]
        task_id = (await client.get(f"/api/v1/jobs/{job_id}")).json()["tasks"][0]["id"]
        configure(client, app_access_code="open-sesame")

        delete = await client.delete(f"/api/v1/jobs/{job_id}")
        retry = await client.post(f"/api/v1/jobs/{job_id}/tasks/{task_id}/retry")

        assert delete.status_code == 401
        assert retry.status_code == 401
        # The batch survived the refused delete.
        assert (await client.get(f"/api/v1/jobs/{job_id}")).status_code == 200

    async def test_reading_a_batch_by_its_id_needs_no_code(self, api) -> None:
        """The id is the capability; export links are plain anchors and send no headers."""
        client, _ = api
        created = await client.post("/api/v1/jobs", files=upload_files(1))
        job_id = created.json()["job_id"]
        configure(client, app_access_code="open-sesame")

        assert (await client.get(f"/api/v1/jobs/{job_id}")).status_code == 200
        assert (await client.get(f"/api/v1/jobs/{job_id}/leads")).status_code == 200
        # 409 here: the card is still queued, so there is nothing to export yet.
        # The property under test is only that the export is not gated.
        assert (await client.get(f"/api/v1/jobs/{job_id}/export.xlsx")).status_code != 401

    async def test_no_code_configured_means_open(self, api) -> None:
        client, _ = api
        configure(client, app_access_code="")
        assert (await client.post("/api/v1/jobs", files=upload_files(1))).status_code == 201


class TestHourlyCardLimit:
    """The batch limit bounds uploads; this bounds the inference they cost."""

    async def test_cards_beyond_the_hourly_limit_are_refused(self, api) -> None:
        client, _ = api
        configure(client, rate_limit_images_per_hour=3)

        first = await client.post("/api/v1/jobs", files=upload_files(2))
        second = await client.post("/api/v1/jobs", files=upload_files(2))

        assert first.status_code == 201
        assert second.status_code == 429
        assert "at most 3 cards" in second.json()["error"]
        assert "1 remain" in second.json()["error"]

    async def test_a_batch_that_fits_is_accepted(self, api) -> None:
        client, _ = api
        configure(client, rate_limit_images_per_hour=3)

        assert (await client.post("/api/v1/jobs", files=upload_files(2))).status_code == 201
        assert (await client.post("/api/v1/jobs", files=upload_files(1))).status_code == 201

    async def test_zero_disables_the_limit(self, api) -> None:
        client, _ = api
        configure(client, rate_limit_images_per_hour=0)

        for _ in range(3):
            assert (await client.post("/api/v1/jobs", files=upload_files(2))).status_code == 201
