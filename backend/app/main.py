"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.router import api_router
from app.config import Settings, get_settings
from app.core.db import dispose_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter
from app.docs import install_docs
from app.services.storage import LocalDiskStorage
from app.vlm.chain import ProviderChain

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    # Built once: each provider holds an HTTP connection pool, and creating
    # them per request would defeat keep-alive to the model servers.
    app.state.chain = ProviderChain.from_settings(settings)
    app.state.storage = LocalDiskStorage(settings.storage_local_path)

    log.info("startup", env=settings.app_env, tiers=app.state.chain.tiers)
    try:
        yield
    finally:
        await app.state.chain.aclose()
        await dispose_engine()
        log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)

    app = FastAPI(
        title="Business Card Lead Extraction",
        description=(
            "Bulk business card ingestion and structured lead extraction using a "
            "self-hosted Qwen vision-language model, with automatic fallback "
            "across inference tiers."
        ),
        version="1.0.0",
        # The stock docs page is CSP-incompatible in production; app.docs
        # serves a same-origin replacement at the same path.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    install_docs(app, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)

    # The SPA is served same-origin by Caddy in production, so CORS is only
    # needed for the Vite dev server on another port.
    if not settings.is_production:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.app_base_url],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_error_handlers(app)
    app.include_router(api_router)
    return app


def _rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    """Explain the limit rather than returning a bare 429."""
    detail = getattr(exc, "detail", "")
    return JSONResponse(
        status_code=429,
        content={
            "error": (
                "too many requests — each card costs real inference time, so "
                "uploads are rate limited. Please try again shortly."
            ),
            "limit": str(detail),
        },
    )


app = create_app()
