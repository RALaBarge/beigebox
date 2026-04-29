#!/usr/bin/env bash
# Thin wrapper for the reflection step. Used by the PreCompact hook as
# `reflect.sh --auto`. Always exits 0 so it never blocks compaction.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/../lib/reflect.py" "$@" || true
exit 0
