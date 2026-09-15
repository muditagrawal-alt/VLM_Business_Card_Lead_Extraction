"""Shared test fixtures.

Unit tests run against in-memory SQLite, which keeps the suite fast and
dependency-free. Behaviour that is genuinely PostgreSQL-specific (SKIP LOCKED
claiming, JSONB querying) is covered by the integration tests instead.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, ClassVar

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite independent of the developer's local configuration.

    Settings reads a .env file and the ambient environment, so a developer
    running the app locally — with the GPU tier disabled, say — would see
    different test results from CI. Tests must assert on the code's defaults,
    not on whatever is in the working copy.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.startswith(
            ("VLM_", "APP_", "POSTGRES_", "WORKER_", "RATE_", "STORAGE_")
        ) or name in {
            "DATABASE_URL",
            "LOG_LEVEL",
            "RETENTION_DAYS",
            "IMAGE_MAX_EDGE_PX",
        }:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="development",
        database_url="sqlite+aiosqlite:///:memory:",
        vlm_cloud_api_key="",
    )


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A session backed by a fresh in-memory schema per test.

    Foreign key enforcement is switched on explicitly: SQLite ignores foreign
    keys by default, which would make ON DELETE CASCADE silently do nothing and
    let cascade tests pass against behaviour PostgreSQL does not share.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        yield s

    await engine.dispose()


@pytest_asyncio.fixture
async def api(session: AsyncSession) -> AsyncIterator[tuple[object, AsyncSession]]:
    """An app wired to the test session, with storage in a temp directory.

    The provider chain is not started: these tests exercise the HTTP layer and
    the database, while extraction itself is covered by the worker and chain
    tests. A card therefore stays queued, which is exactly the state the
    progress endpoint has to render.
    """
    import tempfile
    from pathlib import Path

    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_chain, get_storage
    from app.config import Settings
    from app.core.db import get_session
    from app.core.rate_limit import limiter
    from app.main import create_app
    from app.services.storage import LocalDiskStorage

    # Rate limiting is real production behaviour, but shared per-IP state
    # across tests would make results depend on execution order. It has its
    # own dedicated test.
    limiter.enabled = False

    settings = Settings(
        app_env="development",
        database_url="sqlite+aiosqlite:///:memory:",
        vlm_cloud_api_key="",
    )
    app = create_app(settings)

    with tempfile.TemporaryDirectory() as tmp:
        storage = LocalDiskStorage(Path(tmp))

        async def _session_override() -> AsyncIterator[AsyncSession]:
            yield session
            await session.flush()

        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_chain] = lambda: _StubChain()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, session


class _StubChain:
    """Stands in for the provider chain in HTTP-level tests."""

    tiers: ClassVar[list[str]] = ["gpu", "cpu"]

    async def health(self) -> dict[str, bool]:
        return {"gpu": True, "cpu": True}

    def breaker_snapshot(self) -> list[dict[str, object]]:
        return [{"tier": "gpu", "state": "closed", "consecutive_failures": 0}]

    async def aclose(self) -> None:
        return None
