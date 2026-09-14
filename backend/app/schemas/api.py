"""Request and response models for the HTTP API.

Kept separate from the ORM so the wire format is an explicit decision rather
than whatever the database happens to contain. Fields the user should never see
— storage keys, raw model responses, the client IP hash — simply do not appear
here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import JobStatus, OutputMode, ProviderTier, TaskStatus


class LeadOut(BaseModel):
    """One extracted lead, as the table and the workbook see it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    image_id: UUID

    # The seven required columns.
    first_name: str | None
    last_name: str | None
    position: str | None
    company: str | None
    location: str | None
    phone: str | None
    email: str | None

    website: str | None = None
    address: dict[str, Any] | None = None
    extra_phones: list[dict[str, Any]] | None = None
    extra_emails: list[str] | None = None
    raw_text: str | None = None
    notes: str | None = None

    confidence: dict[str, float] | None = None
    is_duplicate_of: UUID | None = None
    edited_by_user: bool = False
    created_at: datetime


class LeadUpdate(BaseModel):
    """A user correction.

    Every field is optional so a caller can send only what changed. Unset
    fields are left alone, which is why this cannot simply reuse LeadOut.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    position: str | None = Field(default=None, max_length=256)
    company: str | None = Field(default=None, max_length=256)
    location: str | None = Field(default=None, max_length=256)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    website: str | None = Field(default=None, max_length=512)
    notes: str | None = Field(default=None, max_length=2000)


class TaskOut(BaseModel):
    """Per-card progress, including which tier answered."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    image_id: UUID
    status: TaskStatus
    attempts: int
    provider: ProviderTier | None = None
    model: str | None = None
    output_mode: OutputMode | None = None
    latency_ms: int | None = None
    error: str | None = None
    original_filename: str | None = None


class JobOut(BaseModel):
    """Batch status, shaped so the progress view is a single request."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: JobStatus
    total: int
    done: int
    failed: int
    created_at: datetime
    updated_at: datetime


class JobDetail(JobOut):
    """Batch status plus per-card detail and an honest completion estimate."""

    pending: int
    tasks: list[TaskOut] = Field(default_factory=list)
    queue_depth: int = 0
    estimated_seconds_remaining: int | None = Field(
        default=None,
        description=(
            "Derived from the mean latency of cards already finished in this "
            "batch. Null until at least one card has completed, because any "
            "figure before that would be invented."
        ),
    )


class UploadRejection(BaseModel):
    """A file that was refused, and why, so the UI can name it."""

    filename: str
    reason: str


class JobCreated(BaseModel):
    """The result of an upload: what was accepted and what was not."""

    job_id: UUID
    accepted: int
    duplicates: int = Field(
        default=0,
        description=(
            "Files whose content had been uploaded before. They are processed "
            "from the existing extraction instead of being sent to a model again."
        ),
    )
    rejected: list[UploadRejection] = Field(default_factory=list)


class TierHealth(BaseModel):
    tier: str
    healthy: bool
    breaker_state: str | None = None


class HealthOut(BaseModel):
    status: str
    database: bool | None = None
    tiers: list[TierHealth] = Field(default_factory=list)
