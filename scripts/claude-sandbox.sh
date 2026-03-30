#!/usr/bin/env bash
# Launch Claude Code inside the DinD sandbox container.
#
# Usage:
#   ./scripts/claude-sandbox.sh          # start sandbox + drop into Claude
#   ./scripts/claude-sandbox.sh --shell  # start sandbox + drop into plain sh
#
# The sandbox runs Docker-in-Docker: any containers Claude creates live inside
# the sandbox and cannot affect the host Docker engine.
# The /workspace volume is shared so code changes are visible immediately.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

COMPOSE_FILE="docker/docker-compose.yaml"
SERVICE="claude-bot"

# Ensure the sandbox is running
if ! docker compose -f "$COMPOSE_FILE" --profile sandbox ps --status running | grep -q "$SERVICE"; then
    echo "Starting Claude sandbox..."
    docker compose -f "$COMPOSE_FILE" --profile sandbox up -d "$SERVICE"
    echo "Waiting for Docker daemon inside sandbox..."
    sleep 3
fi

if [ "${1:-}" = "--shell" ]; then
    exec docker compose -f "$COMPOSE_FILE" exec -w /workspace "$SERVICE" sh
fi

# Install Claude Code and drop into it
docker compose -f "$COMPOSE_FILE" exec -w /workspace "$SERVICE" sh -c '
    if ! command -v claude >/dev/null 2>&1; then
        echo "Installing Claude Code..."
        apk add --no-cache nodejs npm git
        npm install -g @anthropic-ai/claude-code
    fi
    echo "Launching Claude Code in sandbox..."
    claude
'
