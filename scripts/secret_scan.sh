#!/usr/bin/env bash
# Scan tracked working-tree files with the adjacent Python 3 implementation.
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/secret_scan.py" "$@"
