#!/usr/bin/env bash
# Oracle Cloud bootstrap for the hosted profile (Ampere A1, Ubuntu 24.04, arm64).
#
# Runs the backend only — Caddy, the API, the worker and PostgreSQL — with every
# card read by the hosted tier, so there is no model to download and no GPU to
# find. The SPA can be served from a static host that calls this API directly
# (APP_CORS_ORIGINS); Caddy still serves a copy at this machine's address.
#
# Run it over SSH as root after copying .env to /opt/vlm-leads. Idempotent:
# re-running updates the checkout and restarts the stack.
set -euxo pipefail

REPO_URL="${REPO_URL:-https://github.com/muditagrawal-alt/VLM_Business_Card_Lead_Extraction.git}"
APP_DIR=/opt/vlm-leads
LOG=/var/log/vlm-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

# apt pauses for a needrestart prompt on Ubuntu 22.04+; nobody is there to answer.
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

echo "=== oracle bootstrap started $(date -Is) ==="

# --- Host firewall. Oracle's Ubuntu images reject all inbound traffic except
# SSH in iptables, independently of the VCN security list, so opening 80 and
# 443 in the console is not enough: Let's Encrypt's challenge would never
# arrive and the certificate would never be issued. The rules go in ahead of
# Oracle's REJECT and are persisted across reboots.
apt-get update
apt-get install -y ca-certificates curl git iptables-persistent
reject_line=$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" { print $1; exit }')
for port in 80 443; do
  if ! iptables -C INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null; then
    if [[ -n "$reject_line" ]]; then
      iptables -I INPUT "$reject_line" -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    else
      iptables -A INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    fi
  fi
done
netfilter-persistent save

# --- Docker, from Docker's own repository (it publishes arm64 builds).
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
systemctl enable --now docker

# --- Checkout. The directory already holds .env, so clone in place.
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull --ff-only
else
  mkdir -p "$APP_DIR"
  git -C "$APP_DIR" init -q -b main
  git -C "$APP_DIR" remote add origin "$REPO_URL"
  git -C "$APP_DIR" fetch -q --depth 1 origin main
  git -C "$APP_DIR" checkout -q -B main origin/main
fi

# --- Configuration.
ENV_FILE="$APP_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "FATAL: $ENV_FILE is missing. Copy .env.example, set SITE_ADDRESS, ACME_EMAIL," >&2
  echo "       POSTGRES_PASSWORD and VLM_CLOUD_API_KEY, then re-run." >&2
  exit 1
fi
chmod 600 "$ENV_FILE"
if ! grep -qE '^VLM_CLOUD_API_KEY=.+' "$ENV_FILE"; then
  echo "FATAL: VLM_CLOUD_API_KEY is empty. The hosted profile has no other tier to fall back on." >&2
  exit 1
fi

set_env() {
  if grep -qE "^$1=" "$ENV_FILE"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
  else
    echo "$1=$2" >> "$ENV_FILE"
  fi
}
# No model server runs under this profile. Left enabled, the self-hosted tiers
# would each cost a failed connection per card, and the readiness report —
# which the interface uses to describe itself — would claim a self-hosted model.
set_env VLM_GPU_ENABLED false
set_env VLM_CPU_ENABLED false

# --- Bring the stack up.
cd "$APP_DIR"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f deploy/docker-compose.prod.yml --profile hosted)
"${COMPOSE[@]}" up -d --build

# --- Wait for readiness, asked of the API directly: through Caddy, a request
# for an unconfigured host name can return an empty 200 and look ready.
echo "waiting for the service to become ready"
for _ in $(seq 1 40); do
  if "${COMPOSE[@]}" exec -T api curl -fsS http://localhost:8000/api/v1/ready >/dev/null 2>&1; then
    echo "=== ready $(date -Is) ==="
    "${COMPOSE[@]}" exec -T api curl -s http://localhost:8000/api/v1/ready
    echo
    exit 0
  fi
  sleep 10
done

echo "WARNING: the service did not report ready within about seven minutes." >&2
"${COMPOSE[@]}" ps
"${COMPOSE[@]}" logs --tail 50 api worker
exit 1
