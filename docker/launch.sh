#!/bin/bash
# launch.sh — BeigeBox Docker launcher
# Loads profile choices from ~/.beigebox/config (set by FIRST_RUN.sh)
# Auto-swaps voice → apple on ARM64 macOS
#
# USAGE:
#   ./launch.sh up -d                              # runs with profiles from config
#   ./launch.sh --profile cdp up -d               # CLI arg overrides config
#
# First time: run FIRST_RUN.sh to set up ~/.beigebox/config
#
# MLX NOTE: Docker on Mac does NOT expose Metal to containers.
# The 'apple' profile uses native arm64 images (no Rosetta emulation).
# For true MLX: run Whisper + Kokoro natively on the host and set
# stt_url / tts_url in runtime_config.yaml to http://localhost:9000/:8880.

set -euo pipefail

cd "$(dirname "$0")"

# Load saved config from FIRST_RUN.sh
CONFIG_FILE="$HOME/.beigebox/config"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "[launch.sh] ERROR: No config found at $CONFIG_FILE"
  echo "[launch.sh] Run ./FIRST_RUN.sh first to set up"
  exit 1
fi

source "$CONFIG_FILE"
echo "[launch.sh] Loaded config from $CONFIG_FILE"

# Verify .env exists (should be created by FIRST_RUN.sh)
if [[ ! -f .env ]]; then
  echo "[launch.sh] ERROR: .env not found — FIRST_RUN.sh may have failed"
  exit 1
fi

# Pin an unpinned image tag in docker-compose.yaml by digest.
# Runs only when the image line still contains a mutable tag (no '@sha256:').
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

ARGS=()

# Apply saved profiles from FIRST_RUN.sh config FIRST (before command)
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

echo "[launch.sh] docker compose ${ARGS[*]}"
exec docker compose "${ARGS[@]}"
