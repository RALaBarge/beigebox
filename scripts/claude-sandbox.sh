#!/usr/bin/env bash
# Launch Claude Code inside the sidecar container.
#
# Usage:
#   ./scripts/claude-sandbox.sh          # start sidecar + drop into Claude
#   ./scripts/claude-sandbox.sh --shell  # start sidecar + drop into bash
#   ./scripts/claude-sandbox.sh --build  # rebuild image first, then start
#
# The /workspace volume is the repo root, mounted read-write.
# BeigeBox is reachable at http://beigebox:8000 from inside the container.
# ANTHROPIC_API_KEY must be set in docker/.env

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

COMPOSE_FILE="docker/docker-compose.yaml"
SERVICE="claude-bot"

if [ "${1:-}" = "--build" ]; then
    echo "Rebuilding Claude sidecar image..."
    docker compose -f "$COMPOSE_FILE" --profile sandbox build "$SERVICE"
    shift
fi

# Ensure the sidecar is running
if ! docker compose -f "$COMPOSE_FILE" --profile sandbox ps --status running | grep -q "$SERVICE"; then
    echo "Starting Claude sidecar..."
    docker compose -f "$COMPOSE_FILE" --profile sandbox up -d "$SERVICE"
fi

if [ "${1:-}" = "--shell" ]; then
    exec docker compose -f "$COMPOSE_FILE" exec -w /workspace -u jinx "$SERVICE" bash
fi

exec docker compose -f "$COMPOSE_FILE" exec -w /workspace -u jinx "$SERVICE" claude
