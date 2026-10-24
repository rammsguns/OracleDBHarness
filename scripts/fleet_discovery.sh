#!/usr/bin/env bash
# Observation-only fleet discovery runner wrapper (A8 / P5 preparation).
# Forwards arguments to scripts/fleet_discovery.py using uv.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO

# Ensure uv cache directory is accessible if not set
if [[ -z "${UV_CACHE_DIR:-}" ]] && [[ ! -w "${HOME}/.cache/uv" ]]; then
  export UV_CACHE_DIR="/tmp/uv-cache-${USER}"
fi

cd "$REPO"
exec uv run --extra oracle python scripts/fleet_discovery.py "$@"
