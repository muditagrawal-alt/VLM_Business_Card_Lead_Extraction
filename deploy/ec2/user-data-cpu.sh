#!/usr/bin/env bash
# EC2 bootstrap for the CPU profile (8 GB instance, Ubuntu 24.04).
#
# This is the cost-down deployment: no GPU, the 4B model as the primary tier.
# Accuracy on the evaluation set matches the 8B and a card takes tens of
# seconds rather than a few, at roughly a tenth of the hourly cost. Used when
# GPU credits run low, or when the G-instance quota has not been granted.
#
# Idempotent: re-running updates the checkout and restarts the stack.
set -euxo pipefail

REPO_URL="${REPO_URL:-https://github.com/muditagrawal-alt/VLM_Business_Card_Lead_Extraction.git}"
APP_DIR=/opt/vlm-leads
MODELS_DIR=/opt/models
exec > >(tee -a /var/log/vlm-bootstrap.log) 2>&1

echo "=== bootstrap started $(date -Is) ==="

# Swap matters more here than on the GPU box: the model sits in host RAM, so
# an 8 GB instance has far less headroom.
if [[ ! -f /swapfile ]]; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl git
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

if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

# Only the 4B weights are needed, which is ~3 GB rather than ~13 GB.
mkdir -p "$MODELS_DIR"
MODELS_DIR="$MODELS_DIR" TIERS=cpu "$APP_DIR/scripts/download_models.sh"

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "FATAL: $APP_DIR/.env is missing. Copy .env.example and set SITE_ADDRESS," >&2
  echo "       ACME_EMAIL, POSTGRES_PASSWORD and VLM_CLOUD_API_KEY." >&2
  exit 1
fi
chmod 600 "$APP_DIR/.env"

cd "$APP_DIR"
{
  echo "MODELS_DIR=$MODELS_DIR"
  # llama.cpp mis-detects the usable CPU count inside a container, so it is
  # stated explicitly from the host.
  echo "LLAMA_THREADS=$(nproc)"
  # There is no GPU tier here, so the chain must not waste a timeout on it.
  echo "VLM_GPU_ENABLED=false"
  # One card at a time: llama.cpp on two vCPUs gains nothing from parallel
  # requests and risks memory pressure on an 8 GB box.
  echo "WORKER_CONCURRENCY=1"
} >> .env

docker compose --env-file "$APP_DIR/.env" -f deploy/docker-compose.prod.yml --profile cpu up -d --build

echo "waiting for readiness (the 4B model takes a couple of minutes to load)"
for _ in $(seq 1 40); do
  if curl -fsS http://localhost/api/v1/ready >/dev/null 2>&1; then
    echo "=== ready $(date -Is) ==="
    curl -s http://localhost/api/v1/ready
    exit 0
  fi
  sleep 15
done

echo "WARNING: not ready within 10 minutes." >&2
docker compose --env-file "$APP_DIR/.env" -f deploy/docker-compose.prod.yml --profile cpu ps
docker compose --env-file "$APP_DIR/.env" -f deploy/docker-compose.prod.yml --profile cpu logs --tail 50
exit 1
