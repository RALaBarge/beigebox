#!/bin/bash
# launch.sh — BeigeBox Docker launcher + setup wizard
# Handles first-run setup automatically, then starts the Docker stack.
#
# USAGE:
#   ./launch.sh up -d                # First run: setup wizard, then launch. Later runs: just launch.
#   ./launch.sh --reset up -d        # Re-run setup wizard, then launch
#   ./launch.sh --profile cdp up -d  # CLI arg overrides profiles from config

set -euo pipefail

cd "$(dirname "$0")"

# ─────────────────────────────────────────────────────────────────────────────
# Colors & Constants
# ─────────────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

CONFIG_DIR="$HOME/.beigebox"
CONFIG_FILE="$CONFIG_DIR/config"

# ─────────────────────────────────────────────────────────────────────────────
# Argument Pre-processing: Strip --reset so it never reaches docker compose
# ─────────────────────────────────────────────────────────────────────────────
FORCE_RESET=false
PASSTHROUGH_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--reset" ]]; then
        FORCE_RESET=true
    else
        PASSTHROUGH_ARGS+=("$arg")
    fi
done
set -- "${PASSTHROUGH_ARGS[@]}"

# ─────────────────────────────────────────────────────────────────────────────
# Setup Wizard — Interactive first-run configuration
# ─────────────────────────────────────────────────────────────────────────────
run_setup_wizard() {
    # Guard: Non-interactive stdin (CI, piped input) should fail early
    if [[ ! -t 0 ]]; then
        echo "[launch.sh] ERROR: No config found and stdin is not a terminal."
        echo "[launch.sh] Create ~/.beigebox/config manually or run interactively."
        exit 1
    fi

    # Banner
    echo ""
    echo -e "${BLUE}  BeigeBox Setup Wizard${NC}"
    echo -e "${BLUE}  ═════════════════════${NC}"
    echo ""

    # Auto-detect platform
    PLATFORM=$(uname -s)
    ARCH=$(uname -m)

    if [[ "$PLATFORM" == "Darwin" ]]; then
        DISPLAY_PLATFORM="macOS"
        IS_MACOS=true
    else
        DISPLAY_PLATFORM="Linux"
        IS_MACOS=false
    fi

    if [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
        DISPLAY_ARCH="ARM64"
        IS_ARM64=true
    else
        DISPLAY_ARCH="x86_64"
        IS_ARM64=false
    fi

    echo -e "${GREEN}✓${NC} Detected: $DISPLAY_PLATFORM ($DISPLAY_ARCH)"
    echo ""

    # Question 1: What do you want to do?
    echo -e "${YELLOW}What's your main use case?${NC}"
    echo ""
    echo "  1. LLM inference only (default)"
    echo "  2. + Speech I/O (voice/STT/TTS)"
    echo "  3. + Browser automation (CDP)"
    echo "  4. Everything (voice + browser)"
    echo ""

    read -p "Choose [1-4, default 1]: " -r USE_CASE
    USE_CASE=${USE_CASE:-1}

    PROFILES=""
    case "$USE_CASE" in
        2)
            if $IS_MACOS && $IS_ARM64; then
                PROFILES="apple"
                echo -e "${GREEN}✓${NC} Will add: native arm64 voice I/O"
            else
                PROFILES="voice"
                echo -e "${GREEN}✓${NC} Will add: voice I/O"
            fi
            ;;
        3)
            PROFILES="cdp"
            echo -e "${GREEN}✓${NC} Will add: browser automation"
            ;;
        4)
            if $IS_MACOS && $IS_ARM64; then
                PROFILES="apple,cdp"
                echo -e "${GREEN}✓${NC} Will add: native voice I/O + browser automation"
            else
                PROFILES="voice,cdp"
                echo -e "${GREEN}✓${NC} Will add: voice I/O + browser automation"
            fi
            ;;
        *)
            echo -e "${GREEN}✓${NC} Core only: LLM inference + proxy"
            ;;
    esac

    echo ""

    # Question 2: Models location
    echo -e "${YELLOW}Where should models be stored?${NC}"
    echo ""

    # Scan for existing Ollama installations
    FOUND_EXISTING=""
    if $IS_MACOS; then
        RECOMMENDED_PATH="/Users/$(whoami)/.ollama"
        if [[ -d "$HOME/.ollama/models" ]] && [[ -n "$(ls -A "$HOME/.ollama/models" 2>/dev/null)" ]]; then
            FOUND_EXISTING="$HOME/.ollama"
        fi
    else
        RECOMMENDED_PATH="/home/$(whoami)/.ollama"
        if [[ -d "$HOME/.ollama/models" ]] && [[ -n "$(ls -A "$HOME/.ollama/models" 2>/dev/null)" ]]; then
            FOUND_EXISTING="$HOME/.ollama"
        fi
    fi

    # If we found existing models, ask user
    if [[ -n "$FOUND_EXISTING" ]]; then
        echo "Found existing Ollama models at:"
        echo "  $FOUND_EXISTING"
        echo ""
        echo "Use these models? [Y/n]"
        read -p "> " -r USE_EXISTING
        USE_EXISTING=${USE_EXISTING:-y}

        if [[ "$USE_EXISTING" == "y" || "$USE_EXISTING" == "Y" ]]; then
            OLLAMA_DATA="$FOUND_EXISTING"
            echo -e "${GREEN}✓${NC} Using existing models at $OLLAMA_DATA"
            echo ""
        else
            echo ""
            echo "Custom path? (recommended: $RECOMMENDED_PATH)"
            read -p "> " -r CUSTOM_OLLAMA_DATA
            if [[ -n "$CUSTOM_OLLAMA_DATA" ]]; then
                OLLAMA_DATA="$CUSTOM_OLLAMA_DATA"
            else
                OLLAMA_DATA="$RECOMMENDED_PATH"
            fi
            echo -e "${GREEN}✓${NC} Models → $OLLAMA_DATA"
            echo ""
        fi
    else
        # No existing models found, just ask for path with recommended default
        echo "Use recommended path? [Y/n]"
        echo "  $RECOMMENDED_PATH"
        echo ""
        read -p "> " -r USE_RECOMMENDED
        USE_RECOMMENDED=${USE_RECOMMENDED:-y}

        if [[ "$USE_RECOMMENDED" == "y" || "$USE_RECOMMENDED" == "Y" ]]; then
            OLLAMA_DATA="$RECOMMENDED_PATH"
            echo -e "${GREEN}✓${NC} Models → $OLLAMA_DATA"
            echo ""
        else
            echo ""
            echo "Custom path:"
            read -p "> " -r CUSTOM_OLLAMA_DATA
            if [[ -n "$CUSTOM_OLLAMA_DATA" ]]; then
                OLLAMA_DATA="$CUSTOM_OLLAMA_DATA"
            else
                OLLAMA_DATA="$RECOMMENDED_PATH"
            fi
            echo -e "${GREEN}✓${NC} Models → $OLLAMA_DATA"
            echo ""
        fi
    fi

    echo ""

    # Port configuration
    BEIGEBOX_PORT=1337    # Fixed — always 1337 for web UI

    # Optional: Customize backend ports if needed
    echo -e "${YELLOW}Backend Port Configuration (optional, press Enter for defaults)${NC}"
    echo ""
    echo "Ollama Inference:    [Enter for 11434]"
    read -p "> " -r CUSTOM_OLLAMA_PORT
    OLLAMA_PORT=${CUSTOM_OLLAMA_PORT:-11434}

    echo "Whisper (STT):       [Enter for 9000]"
    read -p "> " -r CUSTOM_WHISPER_PORT
    WHISPER_PORT=${CUSTOM_WHISPER_PORT:-9000}

    echo "Kokoro (TTS):        [Enter for 8880]"
    read -p "> " -r CUSTOM_KOKORO_PORT
    KOKORO_PORT=${CUSTOM_KOKORO_PORT:-8880}

    echo ""
    echo -e "${GREEN}✓${NC} Ports configured:"
    echo "  BeigeBox Web UI → localhost:$BEIGEBOX_PORT (fixed)"
    echo "  Ollama Inference → localhost:$OLLAMA_PORT"
    echo "  Whisper (STT)   → localhost:$WHISPER_PORT"
    echo "  Kokoro (TTS)    → localhost:$KOKORO_PORT"
    echo ""

    # Create config
    mkdir -p "$CONFIG_DIR"

    cat > "$CONFIG_FILE" << EOF
# BeigeBox Config — auto-generated by launch.sh
# Edit directly: $CONFIG_FILE
# To reconfigure: ./launch.sh --reset up -d

PLATFORM=$PLATFORM
ARCH=$ARCH
IS_MACOS=$IS_MACOS
IS_ARM64=$IS_ARM64
PROFILES=$PROFILES
OLLAMA_DATA=$OLLAMA_DATA
OLLAMA_PORT=$OLLAMA_PORT
BEIGEBOX_PORT=$BEIGEBOX_PORT
WHISPER_PORT=$WHISPER_PORT
KOKORO_PORT=$KOKORO_PORT
CHROME_PORT=9222
EOF

    chmod 600 "$CONFIG_FILE"

    # Update docker/.env with values from config
    if [[ ! -f .env ]]; then
        cp env.example .env
    fi

    if [[ "$(uname -s)" == "Darwin" ]]; then
        sed -i '' "s|^OLLAMA_DATA=.*|OLLAMA_DATA=$OLLAMA_DATA|" .env || true
        sed -i '' "s|^BEIGEBOX_PORT=.*|BEIGEBOX_PORT=1337|" .env || true
        sed -i '' "s|^OLLAMA_PORT=.*|OLLAMA_PORT=$OLLAMA_PORT|" .env || true
        sed -i '' "s|^WHISPER_PORT=.*|WHISPER_PORT=$WHISPER_PORT|" .env || true
        sed -i '' "s|^KOKORO_PORT=.*|KOKORO_PORT=$KOKORO_PORT|" .env || true
        sed -i '' "s|^REQUIRE_HASHES=.*|REQUIRE_HASHES=false|" .env || true
    else
        sed -i "s|^OLLAMA_DATA=.*|OLLAMA_DATA=$OLLAMA_DATA|" .env || true
        sed -i "s|^BEIGEBOX_PORT=.*|BEIGEBOX_PORT=1337|" .env || true
        sed -i "s|^OLLAMA_PORT=.*|OLLAMA_PORT=$OLLAMA_PORT|" .env || true
        sed -i "s|^WHISPER_PORT=.*|WHISPER_PORT=$WHISPER_PORT|" .env || true
        sed -i "s|^KOKORO_PORT=.*|KOKORO_PORT=$KOKORO_PORT|" .env || true
        sed -i "s|^REQUIRE_HASHES=.*|REQUIRE_HASHES=false|" .env || true
    fi

    echo ""
    echo -e "${BLUE}  Config saved!${NC}"
    echo -e "${BLUE}  ════════════${NC}"
    echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
# Config Gate: Check if setup is needed, or load existing config
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$FORCE_RESET" == true ]]; then
    echo "[launch.sh] --reset flag detected — re-running setup..."
    run_setup_wizard
elif [[ ! -f "$CONFIG_FILE" ]]; then
    echo "[launch.sh] First run detected — starting setup..."
    echo ""
    run_setup_wizard
else
    # Config exists — try to load it
    set -a
    source "$CONFIG_FILE" 2>/dev/null || {
        echo "[launch.sh] WARNING: Config at $CONFIG_FILE appears corrupt — re-running setup..."
        run_setup_wizard
    }
    set +a

    # Verify all required vars are present
    MISSING=false
    for var in BEIGEBOX_PORT OLLAMA_PORT WHISPER_PORT KOKORO_PORT OLLAMA_DATA; do
        if [[ -z "${!var:-}" ]]; then
            echo "[launch.sh] WARNING: Config is missing $var — re-running setup..."
            MISSING=true
            break
        fi
    done

    if [[ "$MISSING" == true ]]; then
        run_setup_wizard
    fi
fi

# Ensure config vars are loaded for the launch section below
[[ -z "${BEIGEBOX_PORT:-}" ]] && { set -a; source "$CONFIG_FILE"; set +a; }

echo "[launch.sh] Loaded config from $CONFIG_FILE"
echo "[launch.sh] Using: BeigeBox=$BEIGEBOX_PORT, Ollama=$OLLAMA_PORT, Whisper=$WHISPER_PORT, Kokoro=$KOKORO_PORT"

# ─────────────────────────────────────────────────────────────────────────────
# Verify .env exists (should be created by setup wizard or already present)
# ─────────────────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  echo "[launch.sh] WARNING: .env not found — creating from env.example"
  cp env.example .env || {
    echo "[launch.sh] ERROR: Could not copy env.example"
    exit 1
  }
fi

# ─────────────────────────────────────────────────────────────────────────────
# Pin image digest by SHA256 for immutable launches (apple profile)
# ─────────────────────────────────────────────────────────────────────────────
pin_image_digest() {
  local image="$1"
  # Skip if already pinned
  if grep -q "@sha256:" docker-compose.yaml && grep -q "$image" docker-compose.yaml; then
    return 0
  fi
  if ! grep -q "image: ${image}" docker-compose.yaml; then
    return 0
  fi
  echo "[launch.sh] Pinning digest for ${image} ..."
  docker pull "${image}" --quiet
  local digest
  digest=$(docker inspect "${image}" --format='{{index .RepoDigests 0}}' 2>/dev/null || true)
  if [[ -z "$digest" ]]; then
    echo "[launch.sh] WARNING: could not resolve digest for ${image} — running unpinned"
    return 0
  fi
  # Rewrite the image line in docker-compose.yaml (portable sed)
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "s|image: ${image}|image: ${digest}|g" docker-compose.yaml || true
  else
    sed -i "s|image: ${image}|image: ${digest}|g" docker-compose.yaml || true
  fi
  echo "[launch.sh] Pinned: ${digest}"
}

# ─────────────────────────────────────────────────────────────────────────────
# Profile & Command Building — Convert config to docker compose args
# ─────────────────────────────────────────────────────────────────────────────
ARGS=()

# Apply saved profiles from config FIRST (before command)
if [[ -n "${PROFILES:-}" ]]; then
  echo "[launch.sh] Applying saved profiles: $PROFILES"
  # Convert comma-separated to --profile flags
  for profile in ${PROFILES//,/ }; do
    ARGS+=("--profile" "$profile")
  done
fi

# Then append CLI args (e.g., up, -d, etc.)
ARGS+=("${@:+$@}")

HAS_VOICE=false
HAS_APPLE=false

for arg in "${ARGS[@]:-}"; do
  [[ "$arg" == "voice"              || "$arg" == "--profile=voice" ]] && HAS_VOICE=true
  [[ "$arg" == "apple"              || "$arg" == "--profile=apple" ]] && HAS_APPLE=true
done

if [[ "${IS_ARM64:-false}" == true && "$HAS_VOICE" == true && "$HAS_APPLE" == false ]]; then
  echo "[launch.sh] Apple Silicon detected — swapping --profile voice → --profile apple"
  NEW_ARGS=()
  for arg in "${ARGS[@]:-}"; do
    if   [[ "$arg" == "voice"            ]]; then NEW_ARGS+=("apple")
    elif [[ "$arg" == "--profile=voice"  ]]; then NEW_ARGS+=("--profile=apple")
    else NEW_ARGS+=("$arg")
    fi
  done
  ARGS=("${NEW_ARGS[@]}")
fi

FINAL_HAS_VOICE=false; FINAL_HAS_APPLE=false
for arg in "${ARGS[@]:-}"; do
  [[ "$arg" == "voice" || "$arg" == "--profile=voice" ]] && FINAL_HAS_VOICE=true
  [[ "$arg" == "apple" || "$arg" == "--profile=apple" ]] && FINAL_HAS_APPLE=true
done
if [[ "$FINAL_HAS_VOICE" == true && "$FINAL_HAS_APPLE" == true ]]; then
  echo "[launch.sh] WARNING: both 'voice' and 'apple' profiles active — they share ports :9000/:8880"
fi

if [[ "$FINAL_HAS_APPLE" == true ]]; then
  pin_image_digest "fedirz/faster-whisper-server:latest-cpu"
  pin_image_digest "ghcr.io/remsky/kokoro-fastapi-cpu:latest-arm64"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Execute Docker Compose with final args
# ─────────────────────────────────────────────────────────────────────────────
echo "[launch.sh] docker compose ${ARGS[*]}"
exec docker compose "${ARGS[@]}"
