#!/usr/bin/env bash
# Fetches the Swagger UI files the API's documentation page serves same-origin.
#
# The container image does this at build time (backend/Dockerfile); this is
# the equivalent for a development checkout, so /api/docs works locally with
# the production Content-Security-Policy rather than falling back to the CDN.
# The version and checksum must match the Dockerfile.
set -euo pipefail

VERSION=5.33.0
SHA256=434c69385aa02154348e6dcce0076df3a25ed88f673ac16cf4fed3fcf62c3b1b
URL="https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-${VERSION}.tgz"
DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/backend/app/static/swagger-ui}"

if [[ -f "$DEST/swagger-ui-bundle.js" ]]; then
  echo "docs assets already present in $DEST"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "downloading swagger-ui-dist ${VERSION}"
curl -fsSL --retry 3 -o "$tmp/dist.tgz" "$URL"
echo "${SHA256}  $tmp/dist.tgz" | shasum -a 256 -c - >/dev/null

mkdir -p "$DEST"
tar -xzf "$tmp/dist.tgz" -C "$DEST" --strip-components=1 \
  package/swagger-ui-bundle.js package/swagger-ui.css package/favicon-32x32.png package/LICENSE
echo "installed to $DEST"
