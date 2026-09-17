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

# Swagger UI is served same-origin so the documentation page satisfies the
# edge's Content-Security-Policy. Pinned and integrity-checked like any other
# dependency; only the three files the page needs are kept.
ADD --checksum=sha256:434c69385aa02154348e6dcce0076df3a25ed88f673ac16cf4fed3fcf62c3b1b \
    https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-5.33.0.tgz /tmp/swagger-ui-dist.tgz
RUN mkdir -p app/static/swagger-ui \
    && tar -xzf /tmp/swagger-ui-dist.tgz -C app/static/swagger-ui --strip-components=1 \
        package/swagger-ui-bundle.js package/swagger-ui.css package/favicon-32x32.png package/LICENSE \
    && rm /tmp/swagger-ui-dist.tgz
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
