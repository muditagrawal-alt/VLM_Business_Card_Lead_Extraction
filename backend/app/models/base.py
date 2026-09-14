"""Declarative base, shared column types and mixins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator, Uuid

# PostgreSQL gets JSONB (indexable, binary); SQLite — used by the unit tests —
# falls back to plain JSON so the same models run on both.
JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that is always timezone-aware UTC in Python.

    PostgreSQL round-trips an aware datetime; SQLite silently drops the
    offset and hands back a naive one. Without this, the same comparison
    works on one backend and raises "can't compare offset-naive and
    offset-aware datetimes" on the other — a difference that would surface
    in production rather than in the test that should have caught it.

    Values are normalised to UTC on the way in and re-tagged on the way out,
    so callers can rely on aware UTC everywhere.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected datetime, got {type(value).__name__}")
        # A naive value is assumed to be UTC rather than local time: every
        # timestamp this application creates is generated with UTC.
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class UUIDPrimaryKey:
    """Client-generated UUID primary key.

    Generated in Python rather than by the database so the API can return
    identifiers before a flush, and so ids never collide across environments.
    """

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Server-side created/updated timestamps in UTC."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
