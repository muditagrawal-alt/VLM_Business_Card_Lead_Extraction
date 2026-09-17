#!/usr/bin/env bash
# Azure bootstrap for the GPU profile (Standard_NC4as_T4_v3, Ubuntu 24.04).
#
# Azure's stock Ubuntu image ships neither the NVIDIA driver nor the container
# toolkit, which the AWS Deep Learning AMI provides out of the box. This script
# installs those two pieces and then hands over to deploy/ec2/user-data-gpu.sh,
# which is cloud-agnostic from that point on. Run it over SSH as root after
# copying .env to /opt/vlm-leads, exactly like the EC2 scripts.
#
# Idempotent: every step is skipped when its result already exists.
set -euxo pipefail

REPO_URL="${REPO_URL:-https://github.com/muditagrawal-alt/VLM_Business_Card_Lead_Extraction.git}"
APP_DIR=/opt/vlm-leads
LOG=/var/log/vlm-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

# apt on Ubuntu 22.04+ pauses for a needrestart prompt after kernel-adjacent
# installs; there is nobody at the terminal to answer it.
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

echo "=== azure bootstrap started $(date -Is) ==="

# --- Checkout first: the shared script lives in the repo.
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull --ff-only
else
  apt-get update
  apt-get install -y git curl ca-certificates
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

# --- NVIDIA driver. The Microsoft-maintained NvidiaGpuDriverLinux VM extension
# is the preferred route (applied from the CLI before this script runs); this
# is the fallback for a VM that did not get it. Ubuntu's own archive carries a
# server-branch driver that DKMS builds against the running Azure kernel.
if ! command -v nvidia-smi >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ubuntu-drivers-common "linux-headers-$(uname -r)"
  ubuntu-drivers install --gpgpu
  if ! modprobe nvidia; then
    echo "kernel module not loadable yet; rebooting — re-run this script afterwards" >&2
    reboot
  fi
fi
nvidia-smi

# --- Docker. The shared script installs it too, but the container toolkit
# below has to be configured after Docker exists and before the shared
# script's `docker run --gpus all` check, so it is done here.
if ! command -v docker >/dev/null 2>&1; then
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

# --- NVIDIA container toolkit, so containers can see the GPU.
if ! command -v nvidia-ctk >/dev/null 2>&1; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

# --- Everything from here is identical to EC2: GPU-in-container check,
# weights, .env validation, compose up, readiness wait.
exec "$APP_DIR/deploy/ec2/user-data-gpu.sh"
