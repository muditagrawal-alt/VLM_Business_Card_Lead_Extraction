"""Cross-origin access when the SPA is served from a static host.

With the frontend on Vercel and the API on its own host, the browser calls the
API from another origin. Only the configured origin may, it may send exactly
the one custom header the app uses, and no credentials cross.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

VERCEL = "https://leads.vercel.app"


def app_with(**overrides: object):  # type: ignore[no-untyped-def]
    return create_app(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            vlm_cloud_api_key="",
            **overrides,  # type: ignore[arg-type]
        )
    )


async def client_for(**overrides: object) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with(**overrides))
    async with AsyncClient(transport=transport, base_url="http://api.test") as client:
        yield client


def preflight(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-access-code",
    }


class TestConfiguredOrigin:
    async def test_the_static_host_may_upload_with_the_access_code(self) -> None:
        async for client in client_for(app_env="production", app_cors_origins=VERCEL):
            response = await client.options("/api/v1/jobs", headers=preflight(VERCEL))

            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == VERCEL
            assert "x-access-code" in response.headers["access-control-allow-headers"].lower()
            # No cookies are used, so none are allowed across.
            assert "access-control-allow-credentials" not in response.headers

    async def test_an_actual_request_carries_the_allow_origin_header(self) -> None:
        """Without it the browser hides the response, including a 401's message."""
        async for client in client_for(app_env="production", app_cors_origins=VERCEL):
            response = await client.get("/api/v1/health", headers={"Origin": VERCEL})

            assert response.headers["access-control-allow-origin"] == VERCEL

    async def test_a_trailing_slash_in_the_setting_still_matches(self) -> None:
        async for client in client_for(app_env="production", app_cors_origins=f"{VERCEL}/"):
            response = await client.options("/api/v1/jobs", headers=preflight(VERCEL))

            assert response.headers["access-control-allow-origin"] == VERCEL


class TestOtherOrigins:
    async def test_an_unlisted_origin_is_refused(self) -> None:
        async for client in client_for(app_env="production", app_cors_origins=VERCEL):
            response = await client.options(
                "/api/v1/jobs", headers=preflight("https://evil.example")
            )

            assert response.status_code == 400
            assert "access-control-allow-origin" not in response.headers

    async def test_a_same_origin_deployment_sends_no_cors_headers(self) -> None:
        """Caddy serving the SPA and the API together needs none."""
        async for client in client_for(app_env="production"):
            response = await client.get("/api/v1/health", headers={"Origin": VERCEL})

            assert "access-control-allow-origin" not in response.headers

    async def test_development_allows_the_vite_server(self) -> None:
        async for client in client_for(app_env="development", app_base_url="http://localhost:5173"):
            response = await client.options(
                "/api/v1/jobs", headers=preflight("http://localhost:5173")
            )

            assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
