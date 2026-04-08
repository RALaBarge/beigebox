#!/usr/bin/env bash
# claude.sh — Launch Claude Code inside the BeigeBox sandbox sidecar.
# Run from the docker/ directory: ./claude.sh [--shell | --build]
#
# Uses launch.sh for platform-aware compose (Apple Silicon auto-detected).
#
# USAGE:
#   ./claude.sh            # start sidecar + drop into Claude (--dangerously-skip-permissions)
#   ./claude.sh --shell    # start sidecar + drop into bash
#   ./claude.sh --build    # rebuild sidecar image first, then start Claude
#
# The /workspace volume is the repo root, mounted read-write.
# BeigeBox is reachable at http://beigebox:8000 from inside the container.
# ANTHROPIC_API_KEY must be set in docker/.env

set -euo pipefail
cd "$(dirname "$0")"

SERVICE="claude-bot"

if [ "${1:-}" = "--build" ]; then
    echo "[claude.sh] Rebuilding sidecar image…"
    bash launch.sh --profile sandbox build "$SERVICE"
    shift
fi

if ! docker compose --profile sandbox ps --status running 2>/dev/null | grep -q "$SERVICE"; then
    echo "[claude.sh] Starting sidecar…"
    bash launch.sh --profile sandbox up -d "$SERVICE"
fi

if [ "${1:-}" = "--shell" ]; then
    exec docker compose exec -w /workspace -u jinx "$SERVICE" bash
fi

exec docker compose exec -w /workspace -u jinx "$SERVICE" claude --dangerously-skip-permissions
