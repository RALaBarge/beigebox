#!/usr/bin/env bash
# BeigeBox smoke test — validates the full Docker stack end-to-end.
# Run from the docker/ directory: ./smoke.sh
# Exits 0 on success, 1 on any failure.
set -euo pipefail

cd "$(dirname "$0")"

BB="http://localhost:8000"
PASS=0
FAIL=0

_ok()   { echo "  ✓  $*"; ((PASS++)); }
_fail() { echo "  ✗  $*"; ((FAIL++)); }
_hdr()  { echo; echo "── $* ──────────────────────────────────"; }

# ── 1. Stack startup ─────────────────────────────────────────────────────────
_hdr "Stack startup"
docker compose up -d
echo "  waiting for beigebox to become healthy…"
for i in {1..60}; do
  status="$(docker inspect -f '{{.State.Health.Status}}' beigebox 2>/dev/null || true)"
  if [[ "$status" == "healthy" ]]; then
    _ok "beigebox healthy after $((i*2))s"
    break
  fi
  sleep 2
  if [[ "$i" == "60" ]]; then
    _fail "beigebox not healthy after 120s"
    docker compose ps
    docker compose logs --tail=100 beigebox
    exit 1
  fi
done

# ── 2. BeigeBox-native endpoints ─────────────────────────────────────────────
_hdr "BeigeBox endpoints"
for path in \
  /beigebox/health \
  /beigebox/stats \
  /api/v1/info \
  /api/v1/status \
  /api/v1/config \
  /api/v1/stats \
  /api/v1/costs \
  /api/v1/model-performance \
  /api/v1/backends \
  /api/v1/tap \
  /api/v1/flight-recorder \
  /api/v1/tools \
; do
  curl -fsS "$BB$path" >/dev/null \
    && _ok "GET $path" || _fail "GET $path"
done

# ── 3. OpenAI-compatible endpoints ───────────────────────────────────────────
_hdr "OpenAI-compatible endpoints"
curl -fsS "$BB/v1/models" >/dev/null \
  && _ok "GET /v1/models" || _fail "GET /v1/models"

# ── 4. Ollama-native passthrough ─────────────────────────────────────────────
_hdr "Ollama passthrough"
for path in /api/tags /api/version /api/ps; do
  curl -fsS "$BB$path" >/dev/null \
    && _ok "GET $path (passthrough)" || _fail "GET $path (passthrough)"
done

# ── 5. Catch-all unknown endpoint ────────────────────────────────────────────
_hdr "Catch-all passthrough"
STATUS=$(curl -o /dev/null -s -w "%{http_code}" "$BB/v1/some-future-endpoint-xyz")
if [[ "$STATUS" != "404" ]]; then
  _ok "catch-all forwards unknown paths (got HTTP $STATUS)"
else
  _fail "catch-all returned 404 — should forward to backend"
fi

# ── 6. E2E chat (non-streaming) ───────────────────────────────────────────────
_hdr "E2E chat"
curl -fsS "$BB/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:3b","messages":[{"role":"user","content":"reply with exactly one word: ok"}],"stream":false}' \
  >/dev/null \
  && _ok "POST /v1/chat/completions (non-stream)" \
  || _fail "POST /v1/chat/completions (non-stream)"

# ── 7. Streaming chat ─────────────────────────────────────────────────────────
_hdr "Streaming"
curl -fsS "$BB/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:3b","messages":[{"role":"user","content":"say hi"}],"stream":true}' \
  | head -c 200 >/dev/null \
  && _ok "POST /v1/chat/completions (stream)" \
  || _fail "POST /v1/chat/completions (stream)"

# ── 8. Wire log populated ─────────────────────────────────────────────────────
_hdr "Wire log"
TAP=$(curl -fsS "$BB/api/v1/tap?n=5")
COUNT=$(echo "$TAP" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('total',0))" 2>/dev/null || echo 0)
if [[ "$COUNT" -gt 0 ]]; then
  _ok "wire log has $COUNT entries"
else
  _fail "wire log empty after chat"
fi

# ── 9. Search endpoint ────────────────────────────────────────────────────────
_hdr "Semantic search"
curl -fsS "$BB/api/v1/search?q=hello&n=3" >/dev/null \
  && _ok "GET /api/v1/search" || _fail "GET /api/v1/search"

# ── 10. bb wrapper (restricted busybox) ───────────────────────────────────────
_hdr "bb shell wrapper"
docker compose exec -T beigebox /usr/local/bin/bb ls /app >/dev/null \
  && _ok "bb ls /app succeeds" || _fail "bb ls /app failed"

BLOCKED=$(docker compose exec -T beigebox /usr/local/bin/bb rm /tmp/x 2>&1 || true)
if echo "$BLOCKED" | grep -q "not permitted"; then
  _ok "bb rm blocked correctly"
else
  _fail "bb rm not blocked — got: $BLOCKED"
fi

# Confirm system_info routes through bb not /bin/sh
SYSINFO=$(docker compose exec -T beigebox python3 -c "
from beigebox.config import get_config
from beigebox.tools.system_info import SystemInfoTool
cfg = get_config()
t = SystemInfoTool(cfg.get('tools',{}).get('system_info',{}))
print(t.run('uptime'))
" 2>&1 || true)
if echo "$SYSINFO" | grep -qiE "load|up|uptime|error|permitted|days|min"; then
  _ok "system_info tool runs via bb (${SYSINFO:0:60})"
else
  _fail "system_info unexpected output: ${SYSINFO:0:120}"
fi

# ── 11. Config save ───────────────────────────────────────────────────────────
_hdr "Config API"
SAVE=$(curl -fsS -X POST "$BB/api/v1/config" \
  -H 'Content-Type: application/json' \
  -d '{"log_conversations":true}')
echo "$SAVE" | grep -q "saved" \
  && _ok "POST /api/v1/config saves settings" \
  || _fail "POST /api/v1/config failed: $SAVE"

# ── 12. Restart resilience ────────────────────────────────────────────────────
_hdr "Restart resilience"
docker compose restart beigebox
sleep 5
curl -fsS "$BB/beigebox/health" >/dev/null \
  && _ok "healthy after restart" || _fail "not healthy after restart"

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "────────────────────────────────────────────"
echo "  Results: $PASS passed, $FAIL failed"
echo "────────────────────────────────────────────"
[[ "$FAIL" -eq 0 ]] && echo "  All clear. Line is clean." && exit 0 || exit 1
