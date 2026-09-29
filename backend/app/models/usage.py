"""Daily usage counters."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UsageCounter(Base):
    """How many times a named, metered action happened on a UTC day.

    Lives in the database rather than in memory because the ceiling must hold
    across every worker process and survive a restart; a counter that resets
    when the container does is a limit an abuser can reset too.
    """

    __tablename__ = "usage_counters"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
