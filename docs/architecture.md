# Architecture

BeigeBox is a **transparent, modular LLM middleware** — every request flows through a deterministic pipeline with clear separation of concerns.

## Request Pipeline

Every `/v1/chat/completions` request flows through:

```
1. Z-command parsing
2. Pre-request hooks
3. Hybrid routing (session cache → classifier → judge → router)
4. Auto-summarization
5. System context injection
6. Generation param overrides
7. Model option injection
8. Window config application
9. Semantic cache lookup
10. Stream to backend
11. Post-stream transform (WASM)
12. Semantic cache store
```

### In detail

**1. Z-command parsing** (`beigebox/agents/zcommand.py`)
- User prefix: `z: use_openrouter`
- Parsed and removed from message
- Can set model, temperature, backend

**2. Pre-request hooks** (`beigebox/hooks.py`)
- Custom Python/shell scripts run before routing
- Can inspect or modify request

**3. Hybrid routing — Tier 1: Z-commands**
- If user specified `z: llama3.1:8b`, use that backend directly

**3b. Tier 2: Session cache**
- If session has previous context, resume on same model/backend

**3c. Tier 3: Embedding classifier** (`beigebox/agents/embedding_classifier.py`)
- Embed user message
- Cosine similarity against trained classifier centroids
- Outputs: (model, confidence)
- Used if confidence > threshold

**3d. Tier 4: Decision LLM** (`beigebox/agents/decision.py`)
- Small LLM judges borderline requests
- "Should this use llama3.1 or qwen2.5?"
- Runs only if classifier confidence is low

**3e. Tier 5: Multi-backend router** (`beigebox/backends/router.py`)
- Maintains rolling P95 latency window per backend
- Backends exceeding threshold deprioritized
- Weighted random selection for A/B splitting
- Fails over on error

**4. Auto-summarization** (`beigebox/proxy.py:_auto_summarize()`)
- If context window is 80%+ full, summarize earlier turns
- Keeps recent context, compresses history
- Transparent to client

**5. System context injection**
- Loads `system_context.md` (hot-reloaded every request)
- Prepended to system message
- Can include prompting, examples, rules

**6. Generation param overrides** (`_inject_generation_params()`)
- Reads from `runtime_config.yaml` (mtime-checked, no reload needed)
- Applies global defaults: temperature, top_p, repeat_penalty
- Can be overridden per-request

**7. Model option injection** (`_inject_model_options()`)
- Reads per-model options from `config.yaml` `models:` section
- E.g., llama3.1 always uses GPU layers 30/40
- Applied if model is in the config

**8. Window config application** (`_apply_window_config()`)
- Reads `_window_config` from request body (highest priority)
- Overrides system + per-model options
- Can set temperature, keep_alive, num_predict per-pane

**9. Semantic cache lookup** (`beigebox/cache.py:SemanticCache`)
- Embeds the user message
- Searches ChromaDB for similar past requests
- If hit (cosine sim > 0.95), returns cached response
- Saves tokens + latency

**10. Stream to backend**
- HTTP POST to Ollama/OpenRouter/etc.
- If WASM module active, response is buffered
- Otherwise streamed directly to client

**11. Post-stream WASM transform** (`beigebox/wasm_runtime.py`)
- If `wasm.enabled: true` in config, module processes response
- E.g., removes markdown wrapper, normalizes format
- Streams transformed output to client

**12. Semantic cache store**
- After stream completes, store (request, response) in ChromaDB
- Future similar requests hit the cache

## Subsystems

| Module | Purpose |
|---|---|
| `beigebox/main.py` | FastAPI app, lifespan, all endpoints |
| `beigebox/proxy.py` | `Proxy.forward_chat_completion_stream()` — core pipeline |
| `beigebox/app_state.py` | `AppState` dataclass — all subsystem references |
| `beigebox/config.py` | Config loader: static (`config.yaml`) + hot-reload (`runtime_config.yaml`) |
| `beigebox/cache.py` | `SemanticCache`, `EmbeddingCache`, `ToolResultCache` |
| `beigebox/backends/router.py` | `MultiBackendRouter` — latency tracking, A/B split, failover |
| `beigebox/agents/zcommand.py` | Z-command parser (tier 1 routing) |
| `beigebox/agents/embedding_classifier.py` | Cosine similarity classifier (tier 3) |
| `beigebox/agents/decision.py` | Small LLM judge (tier 4) |
| `beigebox/storage/sqlite_store.py` | Conversations, metrics, latency percentiles |
| `beigebox/storage/vector_store.py` | ChromaDB wrapper for embeddings |
| `beigebox/wasm_runtime.py` | WASM/WASI sandbox for transforms |
| `beigebox/web/index.html` | Single-file web UI (no build step) |
| `beigebox/hooks.py` | Hook registry + execution |
| `beigebox/tools/plugin_loader.py` | Auto-discover `*Tool` classes in `plugins/` |

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
- Backends (Ollama, OpenRouter, vLLM, etc.)
- Storage paths
- Model names and per-model options
- Security policies (API key, auth ACLs)
- Feature flags (all disabled by default)

Example:
```yaml
backends:
  ollama:
    url: http://host.docker.internal:11434
  openrouter:
    url: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}

models:
  llama3.1:8b:
    backend: ollama
    gpu_layers: 30

feature_flags:
  semantic_cache:
    enabled: false
  decision_llm:
    enabled: false
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

`MultiBackendRouter` in `backends/router.py`:

- Maintains rolling P95 latency window (100 samples per backend)
- Backends exceeding `latency_p95_threshold_ms` deprioritized to fallback list
- Weighted random selection when `traffic_split` weights are configured
- Falls back through priority-ordered backends on error
- Tracks error counts, not just latency

Example:
```yaml
backends:
  ollama:
    url: http://host.docker.internal:11434
    priority: 1
    latency_p95_threshold_ms: 2000

  openrouter:
    url: https://openrouter.ai/api/v1
    priority: 2
    traffic_split:
      ollama: 70        # 70% to ollama
      openrouter: 30    # 30% to openrouter
```

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

### ChromaDB

Stores embeddings for:
- Semantic cache (request/response pairs)
- RAG document index
- Classifier training data

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
