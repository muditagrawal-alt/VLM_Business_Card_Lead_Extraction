"""The public URL's guardrails.

There are no accounts, so anything reachable is reachable by anyone who has the
link. These tests pin what a stranger can and cannot do with it: read other
people's leads, spend inference, or destroy data.
"""

from __future__ import annotations

from uuid import uuid4

from tests.integration.test_api import upload_files


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
