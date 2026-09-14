"""The versioned API surface."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import export, health, images, jobs, leads

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(jobs.router)
api_router.include_router(leads.router)
api_router.include_router(export.router)
api_router.include_router(images.router)
