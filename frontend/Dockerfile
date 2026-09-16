# syntax=docker/dockerfile:1.7
# The frontend is static once built, so there is no Node in production: the
# compiled assets are copied into the Caddy image that already terminates TLS.
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not frontend/. The runtime stage needs
# deploy/Caddyfile, which lives outside the frontend directory, so every source
# path below is written relative to the repository root and must stay that way.
# Both callers pass the root: CI uses `context: .` and docker-compose.prod.yml
# uses `context: ..` from deploy/.
#
#   docker build -f frontend/Dockerfile .

FROM node:22-slim AS builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
# `npm ci` installs exactly the lockfile, so a deployed build cannot silently
# pick up a different dependency version than the one that was tested.
RUN npm ci

COPY frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY frontend/vite.config.ts frontend/index.html frontend/eslint.config.js ./
COPY frontend/src ./src
RUN npm run build


FROM caddy:2-alpine AS runtime

COPY --from=builder /app/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
