#!/bin/bash
# FIRST_RUN.sh — Legacy setup wizard (archived)
#
# ⚠️  This file is no longer the primary setup tool.
# The setup wizard functionality has been merged into ./launch.sh
#
# This file is preserved for reference and recovery only.
# Use:
#   ./launch.sh up -d              # For first-run setup and launch
#   ./launch.sh --reset up -d      # To reconfigure existing setup
#
# To manually run this script (not recommended):
#   cd docker && bash .setup/FIRST_RUN.sh

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

CONFIG_DIR="$HOME/.beigebox"
CONFIG_FILE="$CONFIG_DIR/config"
ALLOW_RESET="${1:-}"

# Banner
echo ""
echo -e "${BLUE}  BeigeBox Setup Wizard (Legacy)${NC}"
echo -e "${BLUE}  ═══════════════════════════════${NC}"
echo ""
echo "Note: This script's functionality is now in ./launch.sh"
echo "Use: ./launch.sh --reset up -d"
echo ""

# Check if config already exists
if [[ -f "$CONFIG_FILE" && "$ALLOW_RESET" != "--reset" ]]; then
    echo "Found existing config at:"
    echo "  $CONFIG_FILE"
    echo ""
    echo "To preserve your settings, this will skip reconfiguration."
    echo "To reconfigure everything, run: ./launch.sh --reset up -d"
    echo ""
    read -p "Continue with existing config? [Y/n]: " -r USE_EXISTING
    USE_EXISTING=${USE_EXISTING:-y}

    if [[ "$USE_EXISTING" == "y" || "$USE_EXISTING" == "Y" ]]; then
        # Load and verify existing config
        source "$CONFIG_FILE" 2>/dev/null || {
            echo -e "${YELLOW}⚠${NC} Existing config is corrupt. Reconfiguring..."
            ALLOW_RESET="--reset"
        }

        if [[ "$ALLOW_RESET" != "--reset" ]]; then
            echo -e "${GREEN}✓${NC} Using saved configuration from $CONFIG_FILE"
            echo ""
            # Update docker/.env with existing config values
            cd "$(dirname "$0")/.."
            if [[ ! -f .env ]]; then
                cp env.example .env
            fi

            # Sync config to .env (BeigeBox always 1337, sync backend ports)
            if [[ "$(uname -s)" == "Darwin" ]]; then
                sed -i '' "s|^OLLAMA_DATA=.*|OLLAMA_DATA=${OLLAMA_DATA:-}|" .env || true
                sed -i '' "s|^BEIGEBOX_PORT=.*|BEIGEBOX_PORT=1337|" .env || true
                sed -i '' "s|^OLLAMA_PORT=.*|OLLAMA_PORT=${OLLAMA_PORT:-11434}|" .env || true
                sed -i '' "s|^WHISPER_PORT=.*|WHISPER_PORT=${WHISPER_PORT:-9000}|" .env || true
                sed -i '' "s|^KOKORO_PORT=.*|KOKORO_PORT=${KOKORO_PORT:-8880}|" .env || true
                sed -i '' "s|^REQUIRE_HASHES=.*|REQUIRE_HASHES=false|" .env || true
            else
                sed -i "s|^OLLAMA_DATA=.*|OLLAMA_DATA=${OLLAMA_DATA:-}|" .env || true
                sed -i "s|^BEIGEBOX_PORT=.*|BEIGEBOX_PORT=1337|" .env || true
                sed -i "s|^OLLAMA_PORT=.*|OLLAMA_PORT=${OLLAMA_PORT:-11434}|" .env || true
                sed -i "s|^WHISPER_PORT=.*|WHISPER_PORT=${WHISPER_PORT:-9000}|" .env || true
                sed -i "s|^KOKORO_PORT=.*|KOKORO_PORT=${KOKORO_PORT:-8880}|" .env || true
                sed -i "s|^REQUIRE_HASHES=.*|REQUIRE_HASHES=false|" .env || true
            fi
            echo -e "${GREEN}✓${NC} Synced to docker/.env"
            echo ""
            echo -e "${BLUE}  Ready to launch!${NC}"
            echo -e "${BLUE}  ════════════════${NC}"
            echo ""
            echo -e "${YELLOW}Next:${NC} docker/launch.sh up -d"
            echo -e "${YELLOW}Then:${NC} open http://localhost:1337"
            echo ""
            exit 0
        fi
    fi
fi

# Auto-detect platform (for fresh setup)
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
    # Check if user already has Ollama models
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
        # User wants custom path
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
cd "$(dirname "$0")/.."
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
echo -e "${BLUE}  Ready to launch!${NC}"
echo -e "${BLUE}  ════════════════${NC}"
echo ""
echo "Your settings are saved in:"
echo "  $CONFIG_FILE"
echo ""
echo "Note: BeigeBox web UI is always on port 1337"
echo "To modify backend ports or profiles, either:"
echo "  • Edit the config file directly"
echo "  • Run: ./launch.sh --reset up -d"
echo ""
echo -e "${YELLOW}Next:${NC} docker/launch.sh up -d"
echo -e "${YELLOW}Then:${NC} open http://localhost:1337"
echo ""
