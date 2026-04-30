# API Reference

BeigeBox exposes two APIs:

1. **OpenAI-compatible `/v1`** — standard chat completions endpoint (used by all clients)
2. **BeigeBox internal `/api/v1/*`** — observability, benchmarking, operator control (used by web UI and custom integrations)

## OpenAI-compatible (`/v1/chat/completions`)

### Request

```json
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer <api_key>

{
  "model": "llama3.1:8b",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.7,
  "top_p": 0.9,
  "max_tokens": 100,
  "stream": false,
  "_window_config": {
    "session_id": "session-123",
    "force_reload": false,
    "keep_alive": "5m"
  }
}
```

### Response (non-streaming)

```json
{
  "id": "chatcmpl-123",
  "object": "text_completion",
  "created": 1234567890,
  "model": "llama3.1:8b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 8,
    "total_tokens": 18
  }
}
```

### Streaming

Set `"stream": true` and use Server-Sent Events (SSE):

```bash
curl -N -H "Authorization: Bearer <key>" \
  -d '{"model":"llama3.1:8b","messages":[{"role":"user","content":"hi"}],"stream":true}' \
  http://localhost:1337/v1/chat/completions
```

### Window config (BeigeBox-specific)

The `_window_config` object in the request body controls per-pane settings (highest priority):

```json
{
  "_window_config": {
    "session_id": "string",           // Associate with a past session for context
    "force_reload": boolean,          // Evict model from VRAM (keep_alive: 0)
    "keep_alive": "5m",              // Keep model loaded for N duration
    "temperature": 0.7,              // Override default temperature
    "top_p": 0.95,                   // Override top_p
    "num_predict": 100,              // Max tokens
    "repeat_penalty": 1.1            // Prevent repetition
  }
}
```

These override:
- `_inject_model_options()` (from `config.yaml` `models:` section)
- `_inject_generation_params()` (from `runtime_config.yaml` defaults)

## BeigeBox internal (`/api/v1/*`)

### Health & Status

```
GET /beigebox/health
```

Returns `{"status": "ok", "version": "1.9"}`.

```
GET /api/v1/system-metrics
```

Returns VRAM/CPU usage, model load status, latency percentiles.

### Routing & Backend info

```
GET /api/v1/backends
```

List all configured backends (Ollama, OpenRouter, etc.) with health + latency stats.

```
GET /api/v1/models
```

List all available models across all backends.

### Benchmarking

```
POST /api/v1/bench/run
Content-Type: application/json

{
  "models": ["llama3.1:8b", "qwen2.5:7b"],
  "num_runs": 5,
  "num_predict": 120
}
```

Streams SSE events:
- `{"event": "start", ...}`
- `{"event": "warmup", "model": "...", "status": "..."}`
- `{"event": "run", "model": "...", "run": N, "result": {...}}`
- `{"event": "model_done", "summary": {...}}`
- `{"event": "done", "results": [...]}`

Each result includes: `avg_tokens_per_sec`, `median_tokens_per_sec`, `avg_ttft_ms`, per-run breakdown.

### Harness

The Operator endpoints (`POST /api/v1/operator`, `POST /api/v1/operator/stream`,
`GET /api/v1/operator/runs`, `GET/POST /api/v1/operator/notes`,
`GET /api/v1/operator/{run_id}`) and the agentic harness endpoints
(`POST /api/v1/harness/autonomous`, `POST /api/v1/harness/orchestrate`) were
removed in v3. Agent loops moved out of the proxy and now run in whatever
MCP-speaking client is driving — drive BeigeBox tools via `POST /mcp` instead.

What survives on the harness API:

```
POST /api/v1/harness/wiggam        # multi-agent planning consensus (streaming)
POST /api/v1/harness/ralph         # test-driven self-improvement loop (streaming, gated on harness.ralph_enabled)
POST /api/v1/harness/{run_id}/inject   # inject a steering message into an active run
GET  /api/v1/harness/{run_id}      # retrieve a stored harness run by ID
GET  /api/v1/harness               # list recent harness runs
```

Council multi-LLM features:

```
POST /api/v1/council/propose       # proposer + voter pattern (streaming)
POST /api/v1/council/{run_id}/execute
```

### Tap Observability

```
GET /api/v1/logs/events?limit=100&filter=request
```

Query Tap event log. Parameters:
- `limit` — max events
- `filter` — event type (request, route, tool, error, etc.)
- `start_time` — ISO 8601 timestamp

### Configuration

```
GET /api/v1/config
```

Returns current `config.yaml` merged with `runtime_config.yaml` hot-reload values.

```
POST /api/v1/config/reload
```

Force reload `runtime_config.yaml` (normally auto-checked every request).

---

## Error Handling

Errors follow OpenAI format:

```json
{
  "error": {
    "message": "Invalid API key",
    "type": "invalid_request_error",
    "code": "invalid_api_key"
  }
}
```

Common codes:
- `invalid_api_key` — auth failed (401)
- `rate_limit_exceeded` — exceeded key's rpm limit (429)
- `endpoint_not_allowed` — endpoint ACL denied (403)
- `model_not_found` — no matching backend has that model (404)
- `invalid_request_error` — bad request body (400)
- `internal_server_error` — server error (500)

---

## Authentication

Pass API key via one of:

```bash
# Header
curl -H "Authorization: Bearer <key>" http://localhost:1337/v1/chat/completions

# OpenAI-style header
curl -H "api-key: <key>" http://localhost:1337/v1/chat/completions

# Query param
curl "http://localhost:1337/v1/chat/completions?api_key=<key>"
```

See [Authentication](authentication.md) to set up keys.

---

## Rate Limiting

Each API key can have a `rate_limit_rpm` (requests per minute). When exceeded, returns HTTP 429.

Rate limit is a rolling 60-second window per key.

---

## Session Management

To maintain context across multiple requests, use `_window_config.session_id`:

```bash
# First request — establishes session
curl -X POST http://localhost:1337/v1/chat/completions \
  -d '{"model":"llama3.1:8b","messages":[...],"_window_config":{"session_id":"sess-1"}}'

# Second request — continues context from sess-1
curl -X POST http://localhost:1337/v1/chat/completions \
  -d '{"model":"llama3.1:8b","messages":[...],"_window_config":{"session_id":"sess-1"}}'
```

BeigeBox stores the conversation in SQLite and reuses context.

---

## See also

- [CLI](cli.md) — command-line tools and Z-commands
- [Observability](observability.md) — querying logs and metrics
- [Configuration](configuration.md) — per-model options and feature flags
