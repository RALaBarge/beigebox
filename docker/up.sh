#!/bin/bash
# up.sh — Platform-aware docker compose launcher for BeigeBox
# Run from the docker/ directory: ./up.sh [args]
#
# Auto-selects the best voice profile for the current host:
#   arm64 / Apple Silicon  →  --profile apple  (arm64-native images)
#   x86_64 / Linux/WSL     →  --profile voice  (CPU ONNX images)
#
# All other args are forwarded verbatim to 'docker compose'.
#
# USAGE:
#   ./up.sh up -d                              # default stack
#   ./up.sh --profile voice up -d             # voice I/O (auto-picks apple or cpu)
#   ./up.sh --profile cdp --profile voice up -d  # CDP + voice
#
# MLX NOTE: Docker on Mac does NOT expose Metal to containers.
# The 'apple' profile uses native arm64 images (no Rosetta emulation).
# For true MLX: run Whisper + Kokoro natively on the host and set
# stt_url / tts_url in runtime_config.yaml to http://localhost:9000/:8880.

set -euo pipefail

cd "$(dirname "$0")"

ARCH="$(uname -m)"

if [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
  IS_APPLE=true
else
  IS_APPLE=false
fi

ARGS=("$@")
HAS_VOICE=false
HAS_APPLE=false

for arg in "${ARGS[@]}"; do
  [[ "$arg" == "voice"              || "$arg" == "--profile=voice" ]] && HAS_VOICE=true
  [[ "$arg" == "apple"              || "$arg" == "--profile=apple" ]] && HAS_APPLE=true
done

if [[ "$IS_APPLE" == true && "$HAS_VOICE" == true && "$HAS_APPLE" == false ]]; then
  echo "[up.sh] Apple Silicon detected — swapping --profile voice → --profile apple"
  NEW_ARGS=()
  for arg in "${ARGS[@]}"; do
    if   [[ "$arg" == "voice"            ]]; then NEW_ARGS+=("apple")
    elif [[ "$arg" == "--profile=voice"  ]]; then NEW_ARGS+=("--profile=apple")
    else NEW_ARGS+=("$arg")
    fi
  done
  ARGS=("${NEW_ARGS[@]}")
fi

FINAL_HAS_VOICE=false; FINAL_HAS_APPLE=false
for arg in "${ARGS[@]}"; do
  [[ "$arg" == "voice" || "$arg" == "--profile=voice" ]] && FINAL_HAS_VOICE=true
  [[ "$arg" == "apple" || "$arg" == "--profile=apple" ]] && FINAL_HAS_APPLE=true
done
if [[ "$FINAL_HAS_VOICE" == true && "$FINAL_HAS_APPLE" == true ]]; then
  echo "[up.sh] WARNING: both 'voice' and 'apple' profiles active — they share ports :9000/:8880"
fi

echo "[up.sh] docker compose ${ARGS[*]}"
exec docker compose "${ARGS[@]}"
