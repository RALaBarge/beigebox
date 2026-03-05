# BeigeBox API Endpoints

## OpenAI-compatible
- `POST /v1/chat/completions` — chat (streaming + non-streaming)
- `GET  /v1/models` — list available models
- `POST /v1/embeddings` — embeddings passthrough

## BeigeBox-specific
- `GET  /api/v1/status` — subsystem status (WASM, cache, routing)
- `GET  /api/v1/stats` — conversation + embedding counts
- `GET  /api/v1/costs?days=N` — cost breakdown by model/day
- `GET  /api/v1/model-performance?days=N` — P50/P90/P95/P99 latency, TTFT, tok/s
- `GET  /api/v1/backends` — backend health, rolling P95, degraded status
- `GET  /api/v1/search?q=QUERY` — semantic search over conversations
- `GET  /api/v1/tap?n=N` — last N wiretap entries
- `GET  /api/v1/config` — full merged config
- `POST /api/v1/config` — hot-apply config changes
- `GET  /api/v1/openrouter/balance` — remaining OR credit balance
- `POST /api/v1/workspace/upload` — upload file to workspace/in
- `GET  /api/v1/workspace` — list workspace/in and workspace/out
