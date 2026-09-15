#!/usr/bin/env bash
# Download the Qwen3-VL GGUF weights used by the self-hosted inference tiers.
#
# This runs unattended from cloud-init on the server, so it retries rather than
# failing the whole deployment on a transient CDN error — which is exactly what
# happened during development: an 8 GB transfer died partway through with a Xet
# CAS error, leaving the vision projector missing and the model unable to see.
#
# Downloads resume, so a retry costs only the remaining bytes.
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/models}"
REPO_8B="Qwen/Qwen3-VL-8B-Instruct-GGUF"
REPO_4B="Qwen/Qwen3-VL-4B-Instruct-GGUF"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-5}"

# Only the primary tier is mandatory by default. Set TIERS=all to also fetch
# the CPU fallback and the quantisation used for model comparison.
TIERS="${TIERS:-all}"

if ! command -v hf >/dev/null 2>&1; then
  echo "error: the 'hf' CLI is required. Install it with:" >&2
  echo "  uv tool install huggingface_hub" >&2
  exit 1
fi

mkdir -p "$MODELS_DIR"

# The Xet-accelerated transfer is faster but proved fragile on large files;
# the classic HTTP path is what survives an unattended boot.
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"

fetch() {
  local repo="$1" file="$2" attempt
  if [[ -f "$MODELS_DIR/$file" ]]; then
    echo "==> $file already present, skipping"
    return 0
  fi
  for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
    echo "==> $file (attempt $attempt/$MAX_ATTEMPTS)"
    if hf download "$repo" "$file" --local-dir "$MODELS_DIR"; then
      return 0
    fi
    echo "    transfer failed; retrying in $(( attempt * 10 ))s" >&2
    sleep $(( attempt * 10 ))
  done
  echo "error: could not download $file after $MAX_ATTEMPTS attempts" >&2
  return 1
}

# Tier 1 (primary, GPU). The projector is fetched first: without it the model
# loads but cannot see images, which is a far more confusing failure than a
# missing model file.
fetch "$REPO_8B" "mmproj-Qwen3VL-8B-Instruct-F16.gguf"
fetch "$REPO_8B" "Qwen3VL-8B-Instruct-Q8_0.gguf"

if [[ "$TIERS" == "all" ]]; then
  # Tier 2 (CPU fallback).
  fetch "$REPO_4B" "mmproj-Qwen3VL-4B-Instruct-F16.gguf"
  fetch "$REPO_4B" "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
fi

echo
echo "Verifying every model file is a readable GGUF:"
missing=0
for path in "$MODELS_DIR"/*.gguf; do
  [[ -e "$path" ]] || continue
  # GGUF files start with the magic bytes "GGUF". A truncated download often
  # leaves a plausible-looking file, so check the container, not just the size.
  if [[ "$(head -c 4 "$path")" != "GGUF" ]]; then
    echo "  BAD  $(basename "$path") — not a GGUF container, delete and re-run" >&2
    missing=1
  else
    echo "  ok   $(basename "$path") ($(du -h "$path" | cut -f1))"
  fi
done
exit "$missing"
