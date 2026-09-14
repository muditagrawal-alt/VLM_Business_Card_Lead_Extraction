"""The extracted lead — the product of the whole pipeline."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.task import Task


class Lead(Base, UUIDPrimaryKey, TimestampMixin):
    """One contact extracted from one business card.

    Every field is nullable by design. A card that genuinely has no job title
    must produce a null title, not a plausible guess — for lead data, an
    invented value is worse than a blank one.
    """

    __tablename__ = "leads"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )

    # ---- The seven required columns ----
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position: Mapped[str | None] = mapped_column(String(256), nullable=True)
    company: Mapped[str | None] = mapped_column(String(256), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)

    # ---- Everything else the card offered ----
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    extra_phones: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONColumn, nullable=True)
    extra_emails: Mapped[list[str] | None] = mapped_column(JSONColumn, nullable=True)

    # Full transcription, so a reviewer can see what the model actually read.
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-field score driving the amber/red flags in the table and workbook.
    confidence: Mapped[dict[str, float] | None] = mapped_column(JSONColumn, nullable=True)

    # Points at the first lead in the same batch sharing an email or phone.
    is_duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    # Set when a user corrects a field, so evaluation can exclude edited rows
    # and model accuracy is never measured against human-fixed data.
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    job: Mapped[Job] = relationship(back_populates="leads")
    task: Mapped[Task] = relationship(back_populates="lead")

    @property
    def full_name(self) -> str | None:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) if parts else None

    def __repr__(self) -> str:
        return f"<Lead {self.id} {self.full_name!r} @ {self.company!r}>"
