#!/usr/bin/env bash
# Download the Qwen3-VL GGUF weights used by the self-hosted inference tiers.
# Idempotent: existing complete files are skipped by the HuggingFace cache.
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/models}"
REPO_8B="Qwen/Qwen3-VL-8B-Instruct-GGUF"
REPO_4B="Qwen/Qwen3-VL-4B-Instruct-GGUF"

if ! command -v hf >/dev/null 2>&1; then
  echo "error: the 'hf' CLI is required. Install it with:" >&2
  echo "  uv tool install 'huggingface_hub[cli]'" >&2
  exit 1
fi

mkdir -p "$MODELS_DIR"

fetch() {
  local repo="$1" file="$2"
  echo "==> $file"
  hf download "$repo" "$file" --local-dir "$MODELS_DIR"
}

# Tier 2 (CPU fallback) — smallest, fetched first so a slow link still yields
# a working system.
fetch "$REPO_4B" "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
fetch "$REPO_4B" "mmproj-Qwen3VL-4B-Instruct-F16.gguf"

# Tier 1 (primary, GPU).
fetch "$REPO_8B" "Qwen3VL-8B-Instruct-Q8_0.gguf"
fetch "$REPO_8B" "mmproj-Qwen3VL-8B-Instruct-F16.gguf"

# Lower-precision 8B, compared against Q8_0 during model evaluation.
fetch "$REPO_8B" "Qwen3VL-8B-Instruct-Q4_K_M.gguf"

echo
echo "Models in $MODELS_DIR:"
ls -lh "$MODELS_DIR"/*.gguf
