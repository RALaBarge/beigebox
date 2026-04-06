# Routing & Backends

BeigeBox uses a 5-tier routing hierarchy to select which model and backend to use.

## Routing Tiers (in order)

1. **Z-command** — User inline override: `z: use_llama3.1:8b`
2. **Session cache** — Resume on previous model if session has context
3. **Embedding classifier** — Fast cosine similarity against trained centroids
4. **Decision LLM** — Small LLM judges borderline requests (if classifier uncertain)
5. **Multi-backend router** — Default selection with latency tracking + failover

## Multi-backend Router

The final tier maintains:
- **Latency tracking** — rolling P95 window (100 samples per backend)
- **Latency-aware routing** — backends exceeding threshold are deprioritized
- **A/B splitting** — weighted random selection for experimentation
- **Failover** — automatic fallback on error

Example config:

```yaml
backends:
  ollama:
    url: http://ollama:11434
    priority: 1
    latency_p95_threshold_ms: 2000

  openrouter:
    url: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}
    priority: 2
    traffic_split:
      ollama: 70           # 70% of requests
      openrouter: 30       # 30% of requests
```

## Custom Routing Rules

Define rules in `config.yaml` to force model→backend mappings:

```yaml
routing_rules:
  - pattern: "^code_review"           # Request pattern (regex)
    backend: openrouter               # Always use this backend
    model: gpt-4

  - pattern: "^summarize"
    backend: ollama
    model: llama3.1:8b
    temperature: 0.3
```

## Latency-aware Selection

BeigeBox tracks P95 latency per backend:

```
Backend A: P95 = 500ms (healthy) — used
Backend B: P95 = 5000ms (slow) — deprioritized
Backend C: error rate 20% — demoted to fallback
```

When `latency_p95_threshold_ms` is exceeded, a backend drops to fallback priority.

## A/B Traffic Splitting

Send 70% of requests to Ollama, 30% to OpenRouter:

```yaml
backends:
  ollama:
    url: http://ollama:11434
    priority: 1

  openrouter:
    url: https://openrouter.ai/api/v1
    priority: 2

traffic_split:
  ollama: 70
  openrouter: 30
```

Weighted random selection per request. Useful for gradual rollouts or comparing backends.

## Query backend status

```bash
curl http://localhost:1337/api/v1/backends
```

Returns health, latency P95, error counts, current traffic split.

---

See [Architecture](architecture.md#multi-backend-routing) for internals.
