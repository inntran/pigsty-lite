#!/usr/bin/env bash
set -euo pipefail

containerfile="${1:-tests/molecule/Containerfile}"
image_repo="${2:-localhost/molecule-base}"

if ! command -v podman >/dev/null 2>&1; then
  echo "ERROR: podman is required but not found in PATH" >&2
  exit 1
fi

if [ ! -f "$containerfile" ]; then
  echo "ERROR: containerfile not found: $containerfile" >&2
  exit 1
fi

hash="$(sha256sum "$containerfile" | awk '{print $1}')"
hashed_tag="${image_repo}:${hash}"
latest_tag="${image_repo}:latest"

if ! podman image exists "$hashed_tag"; then
  podman build -t "$hashed_tag" -f "$containerfile" .
fi

podman tag "$hashed_tag" "$latest_tag"
echo "Using image: $hashed_tag"
