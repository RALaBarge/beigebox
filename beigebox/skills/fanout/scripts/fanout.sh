#!/usr/bin/env bash
# fanout.sh — thin wrapper around `python3 -m beigebox.skills.fanout`.
# Run from anywhere; this script cds to the repo root so beigebox/ is importable.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# scripts/fanout.sh -> skill -> skills -> beigebox -> repo root
REPO_ROOT="$(cd "$HERE/../../../.." && pwd)"

cd "$REPO_ROOT"
exec python3 -m beigebox.skills.fanout "$@"
