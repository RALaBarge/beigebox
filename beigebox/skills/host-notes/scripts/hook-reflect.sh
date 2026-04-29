#!/usr/bin/env bash
# PreCompact hook wrapper. Claude Code passes a JSON payload on stdin which
# may include {"transcript_path": "..."} or similar. We try several sources
# in order: JSON .transcript_path, env $CLAUDE_CODE_TRANSCRIPT, stdin as raw
# transcript text.
#
# Always exits 0 — a hook failure should never block compaction.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT="$(cat)"
TRANSCRIPT_PATH=""
if command -v jq >/dev/null 2>&1; then
    TRANSCRIPT_PATH="$(printf '%s' "$INPUT" | jq -r '.transcript_path // .transcript // empty' 2>/dev/null || true)"
fi

if [[ -n "$TRANSCRIPT_PATH" && -f "$TRANSCRIPT_PATH" ]]; then
    python3 "$SCRIPT_DIR/../lib/reflect.py" --auto --transcript "$TRANSCRIPT_PATH" 2>/dev/null || true
elif [[ -n "${CLAUDE_CODE_TRANSCRIPT:-}" && -f "$CLAUDE_CODE_TRANSCRIPT" ]]; then
    python3 "$SCRIPT_DIR/../lib/reflect.py" --auto 2>/dev/null || true
elif [[ -n "$INPUT" ]]; then
    printf '%s' "$INPUT" | python3 "$SCRIPT_DIR/../lib/reflect.py" --auto 2>/dev/null || true
fi
exit 0
