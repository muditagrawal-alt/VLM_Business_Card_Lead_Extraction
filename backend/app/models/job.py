"""An upload batch."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.enums import JobStatus

if TYPE_CHECKING:
    from app.models.lead import Lead
    from app.models.task import Task


class Job(Base, UUIDPrimaryKey, TimestampMixin):
    """One bulk upload of business cards.

    Counters are maintained by the worker as tasks settle so the status
    endpoint is a single row read rather than an aggregate over tasks.
    """

    __tablename__ = "jobs"

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=16),
        default=JobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    # server_default guards against a raw INSERT leaving these NULL; the
    # properties below stay None-tolerant because a freshly constructed Job
    # has not been flushed yet and so has no Python-side default applied.
    total: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    done: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    failed: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )

    # Salted hash only — enough to rate limit and audit, never the raw address.
    client_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Retention sweep deletes the job and cascades to tasks, leads and images.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)

    tasks: Mapped[list[Task]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    leads: Mapped[list[Lead]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def pending(self) -> int:
        return max((self.total or 0) - (self.done or 0) - (self.failed or 0), 0)

    @property
    def is_settled(self) -> bool:
        return (self.done or 0) + (self.failed or 0) >= (self.total or 0)

    def __repr__(self) -> str:
        return f"<Job {self.id} {self.status} {self.done}+{self.failed}/{self.total}>"
