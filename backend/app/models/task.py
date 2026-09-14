"""One card extraction, and the durable queue row that drives it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONColumn, TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.enums import OutputMode, ProviderTier, TaskStatus

if TYPE_CHECKING:
    from app.models.image import Image
    from app.models.job import Job
    from app.models.lead import Lead


class Task(Base, UUIDPrimaryKey, TimestampMixin):
    """Queue row for extracting one card.

    Workers claim rows with SELECT ... FOR UPDATE SKIP LOCKED and hold a
    time-bounded lease. If a worker dies mid-card its lease expires and the
    task becomes claimable again, so a crash costs one retry rather than a
    lost card.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        # Supports the claim query: queued tasks, or leases that have expired.
        Index("ix_tasks_claimable", "status", "lease_until"),
        Index("ix_tasks_job_created", "job_id", "created_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=16),
        default=TaskStatus.QUEUED,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # Which tier answered, and how. Surfaced in the UI and the Excel summary
    # so results are never presented as if all cards took the same path.
    provider: Mapped[ProviderTier | None] = mapped_column(
        Enum(ProviderTier, native_enum=False, length=16), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_mode: Mapped[OutputMode | None] = mapped_column(
        Enum(OutputMode, native_enum=False, length=16), nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Unmodified model response, kept for debugging and for re-running the
    # normalisation layer without paying for inference again.
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    job: Mapped[Job] = relationship(back_populates="tasks")
    image: Mapped[Image] = relationship(back_populates="tasks")
    lead: Mapped[Lead | None] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Task {self.id} {self.status} attempts={self.attempts} provider={self.provider}>"
