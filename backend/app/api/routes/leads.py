"""Read and correct extracted leads."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import SessionDep
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.models import Job, Lead
from app.schemas.api import LeadOut, LeadUpdate
from app.services.normalisation import normalise_email, normalise_phone

log = get_logger(__name__)
router = APIRouter(tags=["leads"])


@router.get("/jobs/{job_id}/leads", response_model=list[LeadOut], summary="Batch leads")
async def list_leads(
    job_id: UUID,
    session: SessionDep,
    limit: int = Query(default=200, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[LeadOut]:
    if await session.scalar(select(Job.id).where(Job.id == job_id)) is None:
        raise NotFoundError("that job")

    leads = (
        await session.scalars(
            select(Lead)
            .where(Lead.job_id == job_id)
            .order_by(Lead.created_at)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [LeadOut.model_validate(lead) for lead in leads]


@router.patch("/leads/{lead_id}", response_model=LeadOut, summary="Correct a lead")
async def update_lead(lead_id: UUID, payload: LeadUpdate, session: SessionDep) -> LeadOut:
    """Apply a user's correction.

    Only fields present in the request are changed, so a caller editing one
    cell cannot blank the rest of the row.

    Corrected phones and emails are put through the same normalisation as
    extracted ones, so the export stays consistent whoever supplied the value.
    The row is marked as edited, which lets evaluation exclude it: measuring
    model accuracy against human-corrected data would flatter the model.
    """
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id))
    if lead is None:
        raise NotFoundError("that lead")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return LeadOut.model_validate(lead)

    if changes.get("phone"):
        changes["phone"] = normalise_phone(changes["phone"]) or changes["phone"]
    if changes.get("email"):
        changes["email"] = normalise_email(changes["email"]) or changes["email"]

    for field, value in changes.items():
        setattr(lead, field, value)

    lead.edited_by_user = True
    if lead.confidence:
        # A human-supplied value is not a model guess, so it must not keep
        # being flagged for review.
        lead.confidence = {
            **lead.confidence,
            **{field: 1.0 for field in changes if field in lead.confidence},
        }

    await session.flush()
    log.info("lead_edited", lead_id=str(lead_id), fields=sorted(changes))
    return LeadOut.model_validate(lead)
