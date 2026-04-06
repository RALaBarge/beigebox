#!/bin/bash
# up.sh — Platform-aware docker compose launcher for BeigeBox
#
# Automatically selects the best voice profile for the current host:
#   arm64 / Apple Silicon  →  --profile apple  (native arm64 images, no Rosetta)
#   x86_64 / Linux/WSL     →  --profile voice   (CPU ONNX images)
#
# All other args are forwarded verbatim to 'docker compose'.
#
# USAGE:
#   ./up.sh up -d                              # default stack (auto-detects platform)
#   ./up.sh --profile voice up -d             # voice I/O  (auto-picks apple or cpu)
#   ./up.sh --profile cdp --profile voice up -d  # CDP + voice (auto-picks)
#   ./up.sh --profile sandbox up -d           # Claude Code sidecar
#
# NOTE: Do NOT pass both --profile voice and --profile apple — they share ports.
#       This script handles the swap automatically.
#
# MLX (Apple Silicon):
#   Docker on Mac does NOT expose Metal/Neural Engine to containers.
#   The 'apple' profile uses native arm64 images (already significantly faster).
#   For true MLX GPU acceleration, run Whisper + Kokoro natively on the host:
#
#     pip install faster-whisper uvicorn          # STT
#     pip install kokoro-fastapi[mlx]             # TTS with MLX
#     python -m kokoro_fastapi --port 8880 &
#     python -m faster_whisper_server --port 9000 &
#
#   Then set in runtime_config.yaml:
#     stt_url: "http://localhost:9000"
#     tts_url: "http://localhost:8880"
#
#   And start BeigeBox without the voice/apple profile:
#     ./up.sh up -d

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH="$(uname -m)"

# ── Platform detection ────────────────────────────────────────────────────────
if [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
  IS_APPLE=true
  BEST_VOICE_PROFILE="apple"
  PLATFORM_LABEL="Apple Silicon (arm64)"
else
  IS_APPLE=false
  BEST_VOICE_PROFILE="voice"
  PLATFORM_LABEL="x86_64 / Linux"
fi

# ── Scan args for voice-related profiles ──────────────────────────────────────
ARGS=("$@")
HAS_VOICE=false
HAS_APPLE=false

for arg in "${ARGS[@]}"; do
  [[ "$arg" == "voice"               || "$arg" == "--profile=voice"  ]] && HAS_VOICE=true
  [[ "$arg" == "apple"               || "$arg" == "--profile=apple"  ]] && HAS_APPLE=true
done

# ── Swap 'voice' → 'apple' on Apple Silicon ───────────────────────────────────
if [[ "$IS_APPLE" == true && "$HAS_VOICE" == true && "$HAS_APPLE" == false ]]; then
  echo "[up.sh] $PLATFORM_LABEL detected — swapping --profile voice → --profile apple"
  NEW_ARGS=()
  for arg in "${ARGS[@]}"; do
    if   [[ "$arg" == "voice"            ]]; then NEW_ARGS+=("apple")
    elif [[ "$arg" == "--profile=voice"  ]]; then NEW_ARGS+=("--profile=apple")
    else NEW_ARGS+=("$arg")
    fi
  done
  ARGS=("${NEW_ARGS[@]}")
fi

# ── Warn if both profiles somehow ended up in the args ───────────────────────
FINAL_HAS_VOICE=false; FINAL_HAS_APPLE=false
for arg in "${ARGS[@]}"; do
  [[ "$arg" == "voice" || "$arg" == "--profile=voice" ]] && FINAL_HAS_VOICE=true
  [[ "$arg" == "apple" || "$arg" == "--profile=apple" ]] && FINAL_HAS_APPLE=true
done
if [[ "$FINAL_HAS_VOICE" == true && "$FINAL_HAS_APPLE" == true ]]; then
  echo "[up.sh] WARNING: both 'voice' and 'apple' profiles active — they share ports :9000/:8880"
  echo "[up.sh] Remove one of them to avoid a bind conflict."
fi

# ── Run ───────────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
echo "[up.sh] docker compose ${ARGS[*]}"
exec docker compose "${ARGS[@]}"
