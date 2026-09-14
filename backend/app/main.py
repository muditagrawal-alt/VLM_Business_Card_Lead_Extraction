"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    log.info(
        "startup",
        env=settings.app_env,
        tiers=[name for name, p in settings.provider_chain() if p.enabled],
    )
    yield
    log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)

    app = FastAPI(
        title="Business Card Lead Extraction",
        description=(
            "Bulk business card ingestion and structured lead extraction "
            "using a self-hosted Qwen vision-language model."
        ),
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # The SPA is served same-origin by Caddy in production; only dev needs CORS.
    if not settings.is_production:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.app_base_url],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/api/health", tags=["health"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
