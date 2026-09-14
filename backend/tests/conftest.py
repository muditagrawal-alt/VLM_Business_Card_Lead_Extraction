"""Shared test fixtures.

Unit tests run against in-memory SQLite, which keeps the suite fast and
dependency-free. Behaviour that is genuinely PostgreSQL-specific (SKIP LOCKED
claiming, JSONB querying) is covered by the integration tests instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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
