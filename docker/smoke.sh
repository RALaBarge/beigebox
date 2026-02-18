#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "== compose up =="
docker compose up -d

echo "== wait for health =="
# waits until beigebox container reports healthy
for i in {1..60}; do
  status="$(docker inspect -f '{{.State.Health.Status}}' beigebox 2>/dev/null || true)"
  if [[ "$status" == "healthy" ]]; then
    echo "beigebox healthy"
    break
  fi
  sleep 2
  if [[ "$i" == "60" ]]; then
    echo "beigebox not healthy after 120s"
    docker compose ps
    docker compose logs --tail=200 beigebox
    exit 1
  fi
done

echo "== endpoints =="
curl -fsS http://localhost:8000/beigebox/health >/dev/null
curl -fsS http://localhost:8000/v1/models

echo "     == e2e chat (minimal) =="
curl -fsS http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:3b","messages":[{"role":"user","content":"reply with one word: ok"}]}' | head -c 400; echo

echo "== restart test =="
docker compose restart
sleep 3
curl -fsS http://localhost:8000/beigebox/health >/dev/null

echo "== done =="
