#!/usr/bin/env bash
set -euo pipefail

# Build the three pigsty-lite molecule base images and run smoke checks.
#
# Usage:
#   bin/molecule_images.sh                       # build all three (skip if present)
#   bin/molecule_images.sh common                # build only common
#   bin/molecule_images.sh data                  # build only data (auto-builds common)
#   bin/molecule_images.sh infra                 # build only infra (auto-builds common)
#   REBUILD=1 bin/molecule_images.sh             # force rebuild

if ! command -v podman >/dev/null 2>&1; then
  echo "ERROR: podman is required but not found in PATH" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
images_dir="$repo_root/tests/molecule/images"

build_one() {
  local name="$1"
  local tag="localhost/molecule-base-${name}:latest"
  local containerfile="$images_dir/$name/Containerfile"
  if [ ! -f "$containerfile" ]; then
    echo "ERROR: missing $containerfile" >&2
    exit 1
  fi
  if [ "${REBUILD:-0}" != "1" ] && podman image exists "$tag"; then
    echo "[skip] $tag already present (REBUILD=1 to force)"
    return 0
  fi
  echo "[build] $tag"
  podman build -f "$containerfile" -t "$tag" "$images_dir/$name"
}

smoke_common() {
  local tag="localhost/molecule-base-common:latest"
  podman run --rm "$tag" rpm -q pgbackrest >/dev/null
  podman run --rm "$tag" rpm -q firewalld >/dev/null
  podman run --rm "$tag" rpm -q epel-release >/dev/null
  podman run --rm "$tag" rpm -q pgdg-redhat-repo >/dev/null
  podman run --rm "$tag" id pigsty >/dev/null
  for bin in victoria-metrics-prod victoria-logs-prod vmalert-prod vmagent-prod vlagent-prod; do
    podman run --rm "$tag" "/usr/local/bin/$bin" -version >/dev/null
  done
  echo "[smoke] common OK"
}

smoke_data() {
  local tag="localhost/molecule-base-data:latest"
  for pkg in etcd patroni postgresql18-server pgbouncer haproxy vip-manager; do
    podman run --rm "$tag" rpm -q "$pkg" >/dev/null
  done
  echo "[smoke] data OK"
}

smoke_infra() {
  local tag="localhost/molecule-base-infra:latest"
  for pkg in alertmanager nginx pgbackrest; do
    podman run --rm "$tag" rpm -q "$pkg" >/dev/null
  done
  echo "[smoke] infra OK"
}

targets=("${1:-all}")
if [ "${targets[0]}" = "all" ]; then
  targets=(common data infra)
fi

for t in "${targets[@]}"; do
  case "$t" in
    common) build_one common && smoke_common ;;
    data) build_one common && build_one data && smoke_data ;;
    infra) build_one common && build_one infra && smoke_infra ;;
    *)
      echo "ERROR: unknown target '$t' (want: common|data|infra|all)" >&2
      exit 2
      ;;
  esac
done
