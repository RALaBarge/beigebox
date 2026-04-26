# Observability

BeigeBox logs all request phases to **Tap** — a unified event log queryable via API.

## Event types

| Type | What it logs |
|---|---|
| `request` | Request entry/exit, model, user |
| `route` | Routing decision (which tier, which backend) |
| `cache` | Semantic cache hit/miss, token savings |
| `tool` | Tool execution with inputs + elapsed ms |
| `error` | Errors + stack trace |
| `metric` | Latency, token counts, P95 aggregates |

## Query the event log

```bash
curl "http://localhost:1337/api/v1/logs/events?limit=100&filter=request"
```

Parameters:
- `limit` — max events (default 100)
- `filter` — event type (request, route, cache, tool, error, metric)
- `start_time` — ISO 8601 timestamp
- `model` — filter by model name

Response:
```json
{
  "events": [
    {
      "timestamp": "2026-03-25T10:30:45Z",
      "type": "request",
      "model": "llama3.1:8b",
      "user": "api-key-hash",
      "input_tokens": 150,
      "output_tokens": 42
    }
  ]
}
```

## Web UI log viewer

Open http://localhost:1337 → Tap tab. Real-time event stream with filtering.

## Metrics endpoint

```bash
curl http://localhost:1337/api/v1/system-metrics
```

Returns:
```json
{
  "vram_gb": 8.5,
  "vram_capacity_gb": 24,
  "cpu_percent": 15.2,
  "models_loaded": ["llama3.1:8b"],
  "latency_p95_ms": 450
}
```

## Debugging

### Trace a single request

```bash
curl -v http://localhost:1337/v1/chat/completions \
  -H "Authorization: Bearer sk-..." \
  -d '{"model":"llama3.1:8b","messages":[{"role":"user","content":"hi"}]}'

# Check logs:
curl "http://localhost:1337/api/v1/logs/events?limit=20&filter=request" | jq
```

### Check backend health

```bash
curl http://localhost:1337/api/v1/backends
```

Shows each backend's latency P95, error count, traffic %.

### Tail logs (Docker)

```bash
docker compose logs -f beigebox
```

---

See [API Reference](api-reference.md#observability) for endpoint details.

For the full coverage map (what's emitted today, where the gaps are, and the
decision rubric for adding new events), see
[observability-coverage.md](observability-coverage.md).
