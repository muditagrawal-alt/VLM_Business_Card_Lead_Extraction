# syntax=docker/dockerfile:1.7
# Multi-stage: dependencies are resolved once and the runtime image carries no
# build tooling. The same image runs the API and the worker — they differ only
# in command, so they can never drift out of sync.

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies are installed from the lockfile before the source is copied, so
# a code change does not invalidate the dependency layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

# libheif is required for the HEIC files iPhones produce; curl is used by the
# container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libheif1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# An unprivileged user: nothing in this service needs root, and the storage
# volume is the only path it must write to.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production

WORKDIR /app
COPY --from=builder --chown=app:app /app /app
RUN mkdir -p /app/storage && chown app:app /app/storage

USER app
EXPOSE 8000

# Liveness only — it must not fail because a model server is briefly busy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
