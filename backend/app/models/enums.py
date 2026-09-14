"""Enumerations shared by the ORM models, API schemas and worker."""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    """Lifecycle of an upload batch."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    # Set when every task in the batch failed; partial failure stays COMPLETED
    # with a non-zero failed count, since usable leads were still produced.
    FAILED = "failed"


class TaskStatus(StrEnum):
    """Lifecycle of a single card extraction."""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class ProviderTier(StrEnum):
    """Inference tiers, in fallback order."""

    GPU = "gpu"
    CPU = "cpu"
    CLOUD = "cloud"


class OutputMode(StrEnum):
    """How structured output was obtained, recorded per task.

    Self-hosted tiers use a grammar compiled from the JSON schema, which
    cannot produce invalid output. Hosted APIs may only support weaker modes,
    so the achieved mode is stored to make accuracy comparisons honest.
    """

    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT_ONLY = "prompt_only"
    REPAIRED = "repaired"


class PhoneType(StrEnum):
    MOBILE = "mobile"
    OFFICE = "office"
    FAX = "fax"
    OTHER = "other"
    UNKNOWN = "unknown"
