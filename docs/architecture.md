# Architecture

BeigeBox is a **thin, observable, OpenAI-compatible proxy** with a built-in MCP tool server, conversation memory, and a self-contained web UI. The agentic decision layer (z-commands, embedding classifier, decision LLM, hybrid routing, routing rules, Operator) was deleted in v3 — agent loops moved out of the proxy and now run in whatever MCP-speaking client is driving (Claude Code, custom SDK, IDE plugin, etc.).

## Request Pipeline

Every `/v1/chat/completions` request flows through:

```
1. Auth middleware       (multi-key registry, admin gate, rate limits)
2. Anomaly detection     (request-rate / error-rate / model-switch heuristics)
3. Pre-request hooks     (HookManager: prompt-injection guards, custom scripts)
4. Guardrail input       (allow/deny rules on the user message)
5. Extraction-attack     (OWASP LLM10 detection, observe-only by default)
6. Pipeline injections   (key-strip → summarize → system-context → gen-params → model-options → window)
7. Backend dispatch      (MultiBackendRouter picks provider; RetryableBackendWrapper retries pre-stream)
8. Normalizer seam       (request normalizer in; response normalizer out; transform-log on the wiretap)
9. Response logging      (assistant_content → conversation log + vector store)
10. Post-response hooks  (HookManager: format validation, etc.)
11. Semantic cache store
```

The streaming path follows the same shape but yields chunks back through the response normalizer and never replays after the first chunk has been delivered.

### In detail

**1. Auth middleware** (`beigebox/auth.py`, `main.py:ApiKeyMiddleware`)
- Multi-key registry. Per-key `allowed_endpoints`, `allowed_models`, `rate_limit_rpm`, `admin: bool`.
- Querystring auth was removed in v3 (`?api_key=...` no longer accepted — it leaks to logs/referrers).
- Admin endpoints (`/api/v1/wasm/reload`, toolbox edits) gate on `KeyMeta.admin`.

**2. Anomaly + extraction-attack detection** (`beigebox/security/{anomaly_detector,extraction_detector}.py`)
- Pre-routing checks. Default mode is "warn" — emit to wiretap, don't block.
- Anomaly: rolling per-IP window of request rate / error rate / payload size / latency z-score.
- Extraction: per-session prompt-pattern scoring for OWASP LLM10.

**3. Pre-request hooks** (`beigebox/hooks.py`, `HookManager`)
- Generic, configurable. Each hook runs in priority order, can mutate the body or set `_beigebox_block` to short-circuit.
- The deleted Operator pre/post hooks were one specific consumer of this system; the system itself survives.

**4-6. Pipeline injections** (`beigebox/proxy.py:_run_request_pipeline`)
- Guardrails check the user message first.
- Auto-summarize (if enabled) compresses long conversation history before forwarding.
- `_inject_system_context` prepends `system_context.md` content (hot-reloaded each request).
- `_inject_generation_params` applies runtime defaults for `temperature` / `top_p` / etc., never overrides explicit client values.
- `_inject_model_options` applies per-model defaults from `config.yaml`.
- `_apply_window_config` reads `_window_config` from the request (highest priority), can set keep_alive / num_predict.
- Internal `_bb_*` keys are stripped before the body leaves BeigeBox.

**7. Backend dispatch** (`beigebox/backends/router.py:MultiBackendRouter`)
- Picks a provider for the request's `model` field. Walks priority-ordered backends, matches on each one's `allowed_models` glob patterns.
- Latency-aware: rolling P95 window per backend; demotes slow backends one tier.
- Wrapped in `RetryableBackendWrapper` for exponential backoff on 429/5xx.
- **Streaming-safe rule**: never replay a stream after the first chunk has been yielded — replay would produce duplicated content + two `[DONE]` markers.

**8. Normalizer seam** (`beigebox/{request,response}_normalizer.py`)
- Translates between OpenAI shape and any backend's native shape.
- Emits a `transforms` audit on the wiretap so you can diff what the upstream actually got vs what the client sent.

**9-10. Logging + post-hooks**
- Conversation messages logged to SQLite (`storage/sqlite_store.py`).
- Assistant content embedded + indexed in Postgres+pgvector (`storage/vector_store.py`) for cross-session memory recall.
- Post-response hooks run for format validation, metrics, etc.

**11. Semantic cache** (`beigebox/cache.py:SemanticCache`)
- Stores (user-message-embedding, response) tuples. On future requests, similar messages can hit the cache and skip the backend call entirely.

## Subsystems

| Module | Purpose |
|---|---|
| `beigebox/main.py` | FastAPI app, lifespan, all HTTP endpoints |
| `beigebox/proxy.py` | `Proxy.forward_chat_completion{,_stream}` — the core pipeline |
| `beigebox/auth.py` | `KeyMeta`, `MultiKeyAuthRegistry`, admin gate |
| `beigebox/app_state.py` | `AppState` dataclass — all subsystem references |
| `beigebox/config.py` | Config loader: static (`config.yaml`) + hot-reload (`runtime_config.yaml`) |
| `beigebox/hooks.py` | Generic hook registry + execution |
| `beigebox/cache.py` | `SemanticCache`, `EmbeddingCache`, `ToolResultCache` |
| `beigebox/guardrails.py` | Input/output content allow/deny rules |
| `beigebox/backends/router.py` | `MultiBackendRouter` — per-model provider selection, latency tracking |
| `beigebox/backends/retry_wrapper.py` | `RetryableBackendWrapper` — backoff + streaming-safe failure |
| `beigebox/backends/{openrouter,openai_compat,ollama}.py` | Concrete backends |
| `beigebox/request_normalizer.py`, `response_normalizer.py` | OpenAI-shape canonicalization with transform log |
| `beigebox/wiretap.py` | `WireLog` — dual-write SQLite + JSONL |
| `beigebox/storage/sqlite_store.py` | Conversations, metrics, audit, wire events |
| `beigebox/storage/vector_store.py` | Postgres+pgvector wrapper; cross-session memory |
| `beigebox/storage/backends/{base,postgres,memory}.py` | Pluggable vector backends via `make_backend` factory |
| `beigebox/wasm_runtime.py` | WASM/WASI sandbox for response/text/input transforms |
| `beigebox/security/{anomaly_detector,extraction_detector,audit_logger,enhanced_injection_guard,rag_content_scanner,rag_poisoning_detector,honeypots}.py` | Security telemetry + active defenses |
| `beigebox/mcp_server.py` | MCP server for `/mcp` and `/pen-mcp` tool surfaces |
| `beigebox/tools/registry.py` | `ToolRegistry` — single source of truth for which tools exist |
| `beigebox/tools/plugin_loader.py` | Auto-discover plugin tools at startup |
| `beigebox/web/index.html` | Single-file web UI (no build step) |
| `beigebox/agents/{council.py,ensemble_voter.py,wiggam_planner.py,ralph_orchestrator.py,skill_loader.py}` | Multi-LLM features that survive: Council, Ensemble, Wiggam, Ralph |

## Application State

All subsystems are initialized once at startup and stored in a single `AppState` dataclass. Endpoints access them via `get_state()`:

```python
from beigebox.main import get_state

state = get_state()
state.proxy              # Proxy
state.router            # MultiBackendRouter
state.semantic_cache    # SemanticCache
state.sqlite_store      # SQLiteStore | None
state.harness_injection_queues  # live run_id → asyncio.Queue map
# ... (see app_state.py for all fields)
```

If called before FastAPI lifespan startup completes, raises `RuntimeError`.

## Configuration System

Two files, two loading strategies:

### `config.yaml` — Static, startup only

Loaded once via `get_config()`. Controls:
- Backends (Ollama, OpenRouter, OpenAI-compat, etc.) with per-backend `allowed_models` patterns
- Storage paths (SQLite + Postgres connection string)
- Model registry (`models.profiles`, `models.default`)
- Security policies (auth keys with admin/non-admin distinction, ACLs)
- Feature flags

Example:
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
    allowed_models: ["x-ai/*", "qwen/*", "anthropic/*"]

models:
  default: x-ai/grok-4-fast
  profiles:
    agentic: x-ai/grok-4-fast
    summary: qwen3:4b

features:
  semantic_cache: false
  hooks: true
```

### `runtime_config.yaml` — Hot-reload, every request

Loaded on-demand via `get_runtime_config()`. mtime-checked, no restart needed. Controls:
- Default model
- Default temperature, top_p
- Feature toggles (runtime only)

Example:
```yaml
default_model: llama3.1:8b
default_temperature: 0.7
feature_toggles:
  auto_summarization: true
```

### Per-pane window config

Carried in the request body (`_window_config`), highest priority. Stripped before forwarding to backend. Can set per-pane temperature, keep_alive, etc.

## Multi-backend Routing

See [Routing & Backends](routing.md) for the full picture. Quick recap: `MultiBackendRouter` in `backends/router.py`:

- Picks a provider for the request's `model` field by walking priority-ordered backends and matching `allowed_models` patterns.
- Maintains rolling P95 latency window per backend; demotes slow backends one tier.
- Wrapped in `RetryableBackendWrapper` for exponential backoff on 429/5xx.
- Streaming-safe: never replays after the first chunk has been yielded.

## Web UI

`beigebox/web/index.html` — single self-contained HTML file.

- All CSS/JS inline (no build step)
- Chat panes with independent settings
- Config drawer with runtime toggles
- Tap event log viewer
- Bench sub-tab for inference speed testing
- No dependencies on external CDNs

Edit directly — changes apply on next page refresh.

## Plugins & Extensibility

### Plugins

Drop a `.py` in `plugins/` with a `*Tool` class:

```python
# plugins/my_tool.py
class MyTool:
    def run(self, input: str) -> str:
        return "result"
```

Auto-discovers at startup via `tools/plugin_loader.py`. No code changes needed.

### Hooks

Custom hook scripts in `hooks/`:

```bash
#!/bin/bash
# hooks/on_tool_complete.sh
echo "Tool $TOOL_NAME completed in $ELAPSED_MS ms"
```

Hooks are event-driven — run on tool completion, request routing decisions, etc.

### WASM modules

Drop a compiled `.wasm` (WASI target) in `wasm_modules/`. When active in config, the proxy:
1. Buffers full response from backend
2. Pipes it through the WASM module (stdin → stdout)
3. Re-emits transformed output to client

Example: `wasm_modules/output_normalizer/` (Rust) strips markdown wrappers.

## Storage

### SQLite

Stores:
- Conversations (session_id, turns, metadata)
- Metrics (latency, token counts, cost)
- Tool execution history

Schema includes `misc1`, `misc2` TEXT spares for extensibility.

### Postgres + pgvector

Stores embeddings for:
- Semantic cache (request/response pairs)
- Cross-session memory (the `memory` MCP tool / `bb sweep` CLI)
- RAG document index

(Migrated from ChromaDB in v3 work — `storage/backends/postgres.py` is the live
backend; `chromadb` backend remains for ephemeral/in-memory tests.)

## Observability

All request phases emit to **Tap** (unified event log):

- Request entry/exit
- Routing decisions (which tier, which backend)
- Model selection
- Token usage estimates
- Latency + P95 aggregates
- Tool execution with inputs
- Errors + stack traces

Stored in SQLite, queryable via `/api/v1/logs/events`.

---

## See also

- [Configuration](configuration.md) — all config options explained
- [Deployment](deployment.md) — running in production
- [Security](security.md) — isolation, hardening, threat model
