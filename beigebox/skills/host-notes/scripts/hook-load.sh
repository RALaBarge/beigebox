#!/usr/bin/env bash
# UserPromptSubmit hook wrapper. Claude Code passes a JSON payload on stdin:
# {"session_id": "...", "prompt": "...", ...}
# We extract `prompt`, run detection, print any matching host's notes to
# stdout (which Claude Code injects into the agent's context).
#
# Always exits 0 — a hook failure should never block the user's prompt.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT="$(cat)"
PROMPT=""
if command -v jq >/dev/null 2>&1; then
    PROMPT="$(printf '%s' "$INPUT" | jq -r '.prompt // .user_prompt // empty' 2>/dev/null || true)"
fi
# Fallback: if jq missing or no .prompt key, treat stdin as raw text.
if [[ -z "$PROMPT" ]]; then
    PROMPT="$INPUT"
fi
# Empty prompt → silent exit.
if [[ -z "${PROMPT// /}" ]]; then
    exit 0
fi

python3 "$SCRIPT_DIR/../lib/load.py" --detect "$PROMPT" --once-per-session 2>/dev/null || true
exit 0
