"""SQLAlchemy ORM models.

Importing this package registers every model on `Base.metadata`, which is what
Alembic's autogenerate and the test fixtures rely on.
"""

from app.models.base import Base
from app.models.enums import (
    JobStatus,
    OutputMode,
    PhoneType,
    ProviderTier,
    TaskStatus,
)
from app.models.image import Image
from app.models.job import Job
from app.models.lead import Lead
from app.models.task import Task

__all__ = [
    "Base",
    "Image",
    "Job",
    "JobStatus",
    "Lead",
    "OutputMode",
    "PhoneType",
    "ProviderTier",
    "Task",
    "TaskStatus",
]
