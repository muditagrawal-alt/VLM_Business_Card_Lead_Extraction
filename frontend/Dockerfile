# syntax=docker/dockerfile:1.7
# The frontend is static once built, so there is no Node in production: the
# compiled assets are copied into the Caddy image that already terminates TLS.

FROM node:22-slim AS builder

WORKDIR /app

COPY package.json package-lock.json ./
# `npm ci` installs exactly the lockfile, so a deployed build cannot silently
# pick up a different dependency version than the one that was tested.
RUN npm ci

COPY tsconfig*.json vite.config.ts index.html eslint.config.js ./
COPY src ./src
RUN npm run build


FROM caddy:2-alpine AS runtime

COPY --from=builder /app/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
