#!/usr/bin/env bash
# Thin wrapper: `load.sh <host>` or `load.sh --detect "<text>"`.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/../lib/load.py" "$@"
