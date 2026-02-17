#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# BeigeBox Setup — Tap the line. Own the conversation.
#
# This script:
#   1. Starts the Docker stack (Ollama + BeigeBox + Open WebUI)
#   2. Waits for Ollama to be ready
#   3. Pulls the minimum required models
#   4. Verifies everything is working
#
# Usage:
#   ./setup.sh                    # full stack
#   ./setup.sh --no-gpu           # CPU-only (no NVIDIA GPU)
#   ./setup.sh --skip-models      # don't pull models (already have them)
#   ./setup.sh --model <your-model>  # also pull a specific chat model
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

BANNER='
    ╔══════════════════════════════════════════════════╗
    ║                                                  ║
    ║   ██████  ███████ ██  ██████  ███████            ║
    ║   ██   ██ ██      ██ ██       ██                 ║
    ║   ██████  █████   ██ ██   ███ █████              ║
    ║   ██   ██ ██      ██ ██    ██ ██                 ║
    ║   ██████  ███████ ██  ██████  ███████            ║
    ║                                                  ║
    ║   ██████   ██████  ██   ██                       ║
    ║   ██   ██ ██    ██  ██ ██                        ║
    ║   ██████  ██    ██   ███                         ║
    ║   ██   ██ ██    ██  ██ ██                        ║
    ║   ██████   ██████  ██   ██                       ║
    ║                                                  ║
    ║   Tap the line. Own the conversation.            ║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝
'

# ── Parse args ──────────────────────────────────────────────────────
SKIP_MODELS=false
EXTRA_MODELS=()
NO_GPU=false
OLLAMA_HOST="http://localhost:${OLLAMA_PORT:-11434}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-models) SKIP_MODELS=true; shift ;;
        --no-gpu)      NO_GPU=true; shift ;;
        --model)       EXTRA_MODELS+=("$2"); shift 2 ;;
        *)             echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Required models ─────────────────────────────────────────────────
# These are the minimum models BeigeBox needs to function.
# The decision LLM and embedding model are non-negotiable.
REQUIRED_MODELS=(
    "nomic-embed-text"    # ~270MB — embedding model for ChromaDB
    "nomic-embed-text"        # Required — embedding model for ChromaDB + classifier
)

# Suggest a default chat model if the user didn't specify one
DEFAULT_CHAT_MODEL=""

# ── Helpers ─────────────────────────────────────────────────────────
log()  { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }
info() { echo -e "  ${CYAN}→${NC} $*"; }

wait_for_service() {
    local name="$1" url="$2" max_wait="${3:-60}"
    local elapsed=0
    info "Waiting for ${name}..."
    while ! curl -sf "$url" > /dev/null 2>&1; do
        sleep 2
        elapsed=$((elapsed + 2))
        if [ $elapsed -ge $max_wait ]; then
            err "${name} didn't come up after ${max_wait}s"
            return 1
        fi
    done
    log "${name} is ready"
}

pull_model() {
    local model="$1"
    info "Pulling ${model}..."
    if curl -sf "${OLLAMA_HOST}/api/tags" | grep -q "\"${model}\"" 2>/dev/null; then
        log "${model} already available"
        return 0
    fi
    # Use Ollama's pull API
    curl -sf "${OLLAMA_HOST}/api/pull" -d "{\"name\": \"${model}\"}" | while read -r line; do
        status=$(echo "$line" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
        if [ -n "$status" ]; then
            printf "\r  ${DIM}  %s${NC}          " "$status"
        fi
    done
    echo ""
    log "${model} pulled"
}

# ── Main ────────────────────────────────────────────────────────────
echo -e "${CYAN}${BANNER}${NC}"
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
    err "Docker not found. Install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker compose version &> /dev/null 2>&1; then
    err "Docker Compose not found. Install Docker Compose v2."
    exit 1
fi

log "Docker and Compose found"

# Check NVIDIA GPU (optional)
if [ "$NO_GPU" = false ]; then
    if command -v nvidia-smi &> /dev/null; then
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
        log "GPU detected: ${GPU_NAME}"
    else
        warn "No NVIDIA GPU detected. Models will run on CPU (slower)."
        warn "If you have a GPU, install nvidia-container-toolkit first."
        warn "Rerun with --no-gpu to suppress this warning."
    fi
fi

# Navigate to docker directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/docker"

echo ""
info "Starting Docker stack..."

# Start the stack
if [ "$NO_GPU" = true ]; then
    # Remove GPU reservation for CPU-only mode
    COMPOSE_PROFILES="" docker compose up -d --build 2>&1 | tail -5
else
    docker compose up -d --build 2>&1 | tail -5
fi

echo ""

# Wait for services
wait_for_service "Ollama" "${OLLAMA_HOST}/api/tags" 60
wait_for_service "BeigeBox" "http://localhost:${BEIGEBOX_PORT:-8000}/beigebox/health" 30
wait_for_service "Open WebUI" "http://localhost:${WEBUI_PORT:-3000}" 30

# Pull models
if [ "$SKIP_MODELS" = false ]; then
    echo ""
    info "Pulling required models..."

    for model in "${REQUIRED_MODELS[@]}"; do
        pull_model "$model"
    done

    # Pull extra models requested via --model
    for model in "${EXTRA_MODELS[@]}"; do
        pull_model "$model"
    done

    # Check if user has any chat models already
    EXISTING_MODELS=$(curl -sf "${OLLAMA_HOST}/api/tags" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | grep -v "nomic-embed" || true)
    if [ -z "$EXISTING_MODELS" ]; then
        echo ""
        warn "No chat models found. Pulling default: ${DEFAULT_CHAT_MODEL}"
        pull_model "$DEFAULT_CHAT_MODEL"
    fi
fi

# Verify
echo ""
echo -e "  ${BOLD}${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "  ${BOLD}${GREEN}  BeigeBox is ready!${NC}"
echo -e "  ${BOLD}${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}Open WebUI${NC}   → http://localhost:${WEBUI_PORT:-3000}"
echo -e "  ${CYAN}BeigeBox API${NC} → http://localhost:${BEIGEBOX_PORT:-8000}/beigebox/health"
echo -e "  ${CYAN}Ollama API${NC}   → http://localhost:${OLLAMA_PORT:-11434}"
echo ""
echo -e "  ${DIM}Watch the wire:${NC}  docker exec beigebox python -m beigebox tap"
echo -e "  ${DIM}Check status:${NC}    docker exec beigebox python -m beigebox ring"
echo -e "  ${DIM}View stats:${NC}      docker exec beigebox python -m beigebox flash"
echo -e "  ${DIM}Stop everything:${NC} cd docker && docker compose down"
echo ""

# List available models
echo -e "  ${CYAN}Models available:${NC}"
curl -sf "${OLLAMA_HOST}/api/tags" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | while read -r m; do
    echo -e "    • ${m}"
done
echo ""
