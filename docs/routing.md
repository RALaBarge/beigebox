# Routing & Backends

BeigeBox routes a request by **model name only**. The client says "I want model X"; BeigeBox picks which provider serves model X. The agentic decision layer (z-commands, session cache, embedding classifier, decision LLM, routing rules with body-mutation) was deleted in v3 — that work moved out of the proxy and into whatever MCP-speaking client is driving (Claude Code, custom SDK, etc.).

## What survives

- **`MultiBackendRouter`** (`beigebox/backends/router.py`) — picks a provider for a named model based on `routing.model_routes` and per-backend `allowed_models` in `config.yaml`.
- **`RetryableBackendWrapper`** (`beigebox/backends/retry_wrapper.py`) — exponential backoff for 429 / 5xx, with the streaming-safe rule: never replay after the first chunk has been yielded to the client.
- **Per-backend `forward` / `forward_stream`** in concrete backends (`openrouter.py`, `openai_compat.py`, `ollama.py`, plus optional plugins under `backends/plugins/`).
- **Request + response normalizers** translate to/from OpenAI shape with a transform-log on the wiretap.
- **Latency tracking** — rolling P95 window per backend, used to demote slow providers in the priority order.

## How a chat completion gets routed

1. Client POSTs `/v1/chat/completions` with `model: "x-ai/grok-4-fast"` (or whatever).
2. Auth middleware resolves the API key (if auth is enabled) and stores `KeyMeta` on the request.
3. `proxy.py:_run_request_pipeline` runs guardrails, extraction-attack detection, key-stripping, summarization, system-context injection, generation-params injection, model-options injection.
4. `MultiBackendRouter.forward(body)` looks at `body["model"]`, walks `model_routes` + per-backend `allowed_models` patterns in priority order, and dispatches to the matching backend's `forward` (or `forward_stream`).
5. `RetryableBackendWrapper` wraps the forward call with retry-on-429-or-5xx.
6. The chosen backend (`openrouter.py`, `ollama.py`, etc.) re-shapes the body via the request normalizer, posts to the upstream, and yields the response (or stream chunks) back through the response normalizer.
7. Proxy logs to wiretap, runs post-response hooks + format validation + semantic-cache store, returns to the client.

## Configure backends

```yaml
backends:
  - provider: ollama
    name: ollama-local
    url: http://ollama:11434
    priority: 1
    allowed_models: ["llama3.2:*", "qwen3:*", "nomic-embed-text*"]

  - provider: openrouter
    name: openrouter
    url: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}
    priority: 2
    allowed_models: ["x-ai/*", "qwen/*", "anthropic/*", "google/*", "openai/*"]

routing:
  model_routes:
    - match: "x-ai/*"
      backend: openrouter
    - match: "*:*"            # ollama-style "<name>:<tag>"
      backend: ollama-local
```

Selection rule: highest-priority backend whose `allowed_models` patterns match the request's `model` field. `routing.model_routes` is a fallback / override.

## Latency-aware demotion

The router tracks P95 latency per backend (rolling 100-sample window). When a backend's P95 exceeds `latency_p95_threshold_ms` (configurable per backend), its effective priority drops one tier. When health recovers, priority resets.

```yaml
backends:
  - name: ollama-local
    latency_p95_threshold_ms: 2000   # demote if P95 > 2s
```

## Streaming-safe failure

`forward_stream` in `RetryableBackendWrapper` retries only **before** the first chunk is yielded. After that, any failure propagates to the client immediately — replaying a stream after partial bytes have shipped would produce duplicated content + two `[DONE]` markers, which is worse than failing cleanly.

## Backend plugins

Drop a Python file in `beigebox/backends/plugins/` that subclasses `BaseBackend` (`backends/base.py`). The plugin loader auto-registers it on startup. See `backends/plugins/llama_cpp.py`, `executorch.py`, `mini_sglang.py` for examples.

## Query backend status

```bash
curl http://localhost:1337/api/v1/backends
```

Returns each backend's health, P95 latency, error count, and current effective priority.

---

See [Architecture](architecture.md) for the full pipeline and subsystem map.
