"""Liveness, readiness and operational statistics."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from app.api.deps import ChainDep, SessionDep
from app.models import Lead, Task, TaskStatus
from app.schemas.api import HealthOut, TierHealth

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut, summary="Liveness")
async def health() -> HealthOut:
    """Is the process up. Deliberately cheap — no database, no model.

    Used by the container healthcheck, which must not restart a container
    merely because a dependency is briefly unavailable.
    """
    return HealthOut(status="ok")


@router.get("/ready", response_model=HealthOut, summary="Readiness")
async def ready(session: SessionDep, chain: ChainDep, response: Response) -> HealthOut:
    """Can the service actually do its job.

    Ready means the database answers and at least one inference tier is
    reachable. Requiring every tier would report a healthy service as down
    whenever the optional hosted tier was unreachable.
    """
    try:
        await session.execute(select(1))
        database_ok = True
    # A readiness probe must report the failure, never raise it.
    except Exception:
        database_ok = False

    tier_health = await chain.health()
    breakers = {snap["tier"]: snap["state"] for snap in chain.breaker_snapshot()}
    tiers = [
        TierHealth(tier=name, healthy=healthy, breaker_state=str(breakers.get(name, "unknown")))
        for name, healthy in tier_health.items()
    ]

    ok = database_ok and any(t.healthy for t in tiers)
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthOut(status="ready" if ok else "not ready", database=database_ok, tiers=tiers)


@router.get("/stats", summary="Processing statistics")
async def stats(session: SessionDep, chain: ChainDep) -> dict[str, object]:
    """Per-tier throughput and latency.

    This is the source for the figures quoted in the README, so they come from
    what the system actually did rather than from a benchmark run by hand.
    """
    rows = (
        await session.execute(
            select(
                Task.provider,
                Task.model,
                func.count(Task.id),
                func.avg(Task.latency_ms),
                func.min(Task.latency_ms),
                func.max(Task.latency_ms),
            )
            .where(Task.status == TaskStatus.DONE, Task.provider.is_not(None))
            .group_by(Task.provider, Task.model)
        )
    ).all()

    return {
        "tiers": [
            {
                "tier": provider.value if provider is not None else None,
                "model": model,
                "cards": count,
                "latency_ms": {
                    "mean": int(mean) if mean is not None else None,
                    "min": minimum,
                    "max": maximum,
                },
            }
            for provider, model, count, mean, minimum, maximum in rows
        ],
        "breakers": chain.breaker_snapshot(),
        "totals": {
            "leads": await session.scalar(select(func.count()).select_from(Lead)) or 0,
            "cards_done": await session.scalar(
                select(func.count()).select_from(Task).where(Task.status == TaskStatus.DONE)
            )
            or 0,
            "cards_failed": await session.scalar(
                select(func.count()).select_from(Task).where(Task.status == TaskStatus.FAILED)
            )
            or 0,
        },
    }
