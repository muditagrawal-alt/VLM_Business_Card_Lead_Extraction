#!/usr/bin/env bash
# EC2 bootstrap for the GPU profile (g4dn.xlarge, Ubuntu 24.04).
#
# Intended for use as cloud-init user data on the AWS Deep Learning Base OSS
# Nvidia Driver AMI, which ships a matched NVIDIA driver, Docker and
# nvidia-container-toolkit. Installing those by hand is the single most common
# way a GPU deployment fails, so the AMI choice is deliberate.
#
# The script is idempotent: re-running it updates the checkout and restarts the
# stack rather than duplicating anything.
set -euxo pipefail

REPO_URL="${REPO_URL:-https://github.com/muditagrawal-alt/VLM_Business_Card_Lead_Extraction.git}"
APP_DIR=/opt/vlm-leads
MODELS_DIR=/opt/models
LOG=/var/log/vlm-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

echo "=== bootstrap started $(date -Is) ==="

# --- Swap: insurance against an OOM kill, not a performance feature. The
# model is mlocked, so swap is there to absorb a transient spike rather than
# to be used continuously.
if [[ ! -f /swapfile ]]; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- Docker, only if the AMI did not provide it.
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl git
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
systemctl enable --now docker

# --- Confirm the GPU is actually usable from a container before going further.
# Failing here with a clear message beats discovering it when the first card
# silently falls through to the CPU tier.
if ! nvidia-smi; then
  echo "FATAL: no NVIDIA driver. Use a Deep Learning Base AMI or install the driver." >&2
  exit 1
fi
if ! docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi; then
  echo "FATAL: the container runtime cannot see the GPU (nvidia-container-toolkit)." >&2
  exit 1
fi

# --- Application checkout.
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

# --- Model weights, on their own directory so a redeploy never re-downloads
# 10 GB. This is the slowest step by far; the script retries internally.
mkdir -p "$MODELS_DIR"
MODELS_DIR="$MODELS_DIR" "$APP_DIR/scripts/download_models.sh"

# --- Configuration. The .env must already exist (uploaded out of band or
# written by the deploy workflow): secrets do not belong in user data, which
# is readable from the instance metadata service.
if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "FATAL: $APP_DIR/.env is missing. Copy .env.example, set SITE_ADDRESS," >&2
  echo "       ACME_EMAIL, POSTGRES_PASSWORD and VLM_CLOUD_API_KEY, then re-run." >&2
  exit 1
fi
chmod 600 "$APP_DIR/.env"

# --- Bring the stack up.
cd "$APP_DIR"
{
  echo "MODELS_DIR=$MODELS_DIR"
  # llama.cpp mis-detects the usable CPU count inside a container, so it is
  # stated explicitly from the host. Matters for the CPU fallback tier, which
  # runs alongside the GPU one.
  echo "LLAMA_THREADS=$(nproc)"
} >> .env
docker compose --env-file "$APP_DIR/.env" -f deploy/docker-compose.prod.yml --profile gpu up -d --build

# --- Wait for readiness rather than declaring success on `up`. Loading 8 GB of
# weights takes minutes, and a deploy that returns before the service works is
# worse than one that takes longer.
echo "waiting for the service to become ready (model load takes a few minutes)"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost/api/v1/ready >/dev/null 2>&1; then
    echo "=== ready $(date -Is) ==="
    curl -s http://localhost/api/v1/ready
    exit 0
  fi
  sleep 15
done

echo "WARNING: the service did not report ready within 15 minutes." >&2
docker compose --env-file "$APP_DIR/.env" -f deploy/docker-compose.prod.yml --profile gpu ps
docker compose --env-file "$APP_DIR/.env" -f deploy/docker-compose.prod.yml --profile gpu logs --tail 50
exit 1
