# Session Notes — Mar 04 2026

## What was accomplished

### Symphony-derived backend improvements

Three improvements adapted from analysis of the Symphony Elixir orchestration daemon:

**1. Stream stall detection (`beigebox/backends/retry_wrapper.py`)**
- Added `StreamStallError` exception class
- Added `_stall_guarded()` module-level async generator — wraps any async iterator
  with a per-token timeout via `asyncio.wait_for(aiter.__anext__(), timeout)`
- `forward_stream()` now wraps every backend stream through `_stall_guarded()`
- Stall timeout configurable via `advanced.stream_stall_timeout_seconds` (default 30s)
- A stall is treated as a retryable failure — feeds into the existing exponential
  backoff path the same as a 5xx error

**2. Pydantic config validation (`beigebox/config.py`)**
- Added `_KNOWN_TOP_LEVEL_KEYS` set of all valid top-level config keys
- Added Pydantic model classes (`_ServerCfg`, `_BackendCfg`, `_DecisionLLMCfg`,
  `_OperatorCfg`, `_GenerationCfg`, `_CostTrackingCfg`, `_AutoSumCfg`, `_BeigeBoxConfig`)
  all with `extra='allow'` so unknown sub-keys never break anything
- Added `_validate_config(cfg)` — warns on unknown top-level keys and type mismatches
- Called in `load_config()` after env var resolution; warnings only, never blocks startup

**3. Operator retry continuation (`beigebox/agents/operator.py`)**
- `_chat()` now retries up to 2× with exponential backoff (`1.5^attempt`) on any
  transient error before re-raising — added `import time`
- Main loop injects a wrap-up nudge message at `iteration == max_iter - 2`:
  "You are approaching the maximum number of steps. Please synthesise what you
  have found so far and provide a final answer now."
  Prevents burning the last step on another tool call with no answer

### Web UI — OpenRouter π links

Two π (pi) anchor links added to model names linking to `https://openrouter.ai/<model-id>`:
- **Dashboard → Model Performance table**: inline after model name, only for
  OpenRouter-style models (containing `/` in the ID)
- **Config → OpenRouter model list**: between model name and context length column

Path encoding fix: `id.split('/').map(encodeURIComponent).join('/')` preserves the
`/` separator so links resolve to the correct page rather than a search query.

### Web UI — π vi-mode button centered

`#vi-toggle` CSS changed from `left: 14px` to `left: 50%; transform: translateX(-50%)`
so the button sits at the horizontal center of the screen bottom edge.

### OpenRouter credit balance on Dashboard

New `/api/v1/openrouter/balance` endpoint proxies `https://openrouter.ai/api/v1/auth/key`
using the configured OR backend's API key. Returns remaining credit, usage, and key label.

Dashboard fetches it in the `Promise.allSettled` block alongside other dashboard data.
Renders an "OpenRouter Balance" section with stat cards (Remaining, Used, Key label)
above the cost tracking section. Only appears when an OR backend is configured.

### Workspace: tmpfs + browser drag-and-drop upload

**docker-compose.yaml** — `workspace` volume split:
- `workspace/out` → bind-mount (persistent, agents write results here)
- `workspace/in` → tmpfs (512 MB cap, RAM-only, auto-cleared on container stop)
  Files dropped in for agent processing never touch disk — good for sensitive documents.

**New `POST /api/v1/workspace/upload` endpoint** (`main.py`):
- Accepts multipart file upload, writes to `workspace/in/`
- Path traversal guard (same pattern as workspace delete endpoint)
- `UploadFile` import added to fastapi imports

**Dashboard workspace panel** — IN panel now has full drag-and-drop:
- `ondragover` highlights border with lavender on hover
- `ondragleave` resets border
- `ondrop` calls `uploadWorkspaceFiles(event.dataTransfer.files)`
- "drop or click to upload" link opens a hidden `<input type="file" multiple>`
- `uploadWorkspaceFiles(files)` iterates files, POSTs each as FormData,
  refreshes dashboard after, alerts on any failures
- Empty state shows "(empty — tmpfs, cleared on restart)" as a hint

---

## Test coverage assessment

### smoke.sh — integration/E2E (docker/smoke.sh)
19 sections covering ~40 assertions:
stack startup, core API endpoints (~11), OpenAI compat, Ollama passthrough,
catch-all, E2E chat (stream + non-stream), wire log, conversation storage,
semantic search, config save + hot-reload, generation params, system context,
export formats, audio routing, bb wrapper hardening, restart resilience,
WASM status shape, workspace directory presence.

**Gaps in smoke.sh:** no tests for operator, harness, ensemble, backends failover
in practice, auth middleware, semantic cache, WASM transform execution, or any
of the new endpoints added today (balance, workspace upload).

### Unit tests (tests/)
17 test files, ~3000 lines:

| File | Covers |
|---|---|
| test_backends.py | BackendResponse, OllamaBackend, OpenRouterBackend, MultiBackendRouter (routing, failover, latency, A/B) |
| test_costs.py | CostTracker, schema, per-request + per-model accumulation |
| test_decision.py | DecisionAgent parsing, fallback, disabled state |
| test_harness.py | HarnessOrchestrator events, round cap, retry, error classification |
| test_hooks.py | HookManager pre/post hooks |
| test_model_advertising.py | Model name transformation (advertise/hidden modes) |
| test_new_tools.py | Calculator, datetime, system_info, memory tools |
| test_orchestrator.py | ParallelOrchestrator task dispatch |
| test_proxy_injection.py | `_inject_generation_params`, JSON parsing/recovery |
| test_proxy.py | Message data model |
| test_replay.py | ConversationReplayer |
| test_storage.py | SQLiteStore CRUD, migrations |
| test_system_context.py | Hot-reload, inject_system_context, read/write |
| test_tools.py | Google search mock/real mode |
| test_v08.py | fork_conversation, model perf, prompt injection hook, cost sentinel, proxy block |
| test_web_ui.py | Config endpoint, vi mode toggle, static file serving |
| test_zcommand.py | Z-command parser (all directives) |

### Estimated overall coverage: ~55–60%

**Well covered:** routing layer, storage, tools, z-commands, decision agent,
hooks, harness/orchestrator, proxy injection, replay, system context, model advertising,
web UI config/vi, costs, data models.

**Not covered by unit tests:**
- `cache.py` — SemanticCache, EmbeddingCache, ToolResultCache (no unit tests)
- `wasm_runtime.py` — WASM loading and transform execution
- `summarizer.py` — auto-summarization
- `wiretap.py` — structured wire logging
- `retry_wrapper.py` — stall detection, backoff (added today)
- `operator.py` — full agent loop, retry logic (modified today)
- Config Pydantic validation (added today)
- Workspace upload endpoint (added today)
- OpenRouter balance endpoint (added today)
- Embedding classifier (`agents/embedding_classifier.py`)
- Vector store (`storage/vector_store.py`, ChromaDB) — skipped due to dependency
- Full streaming pipeline end-to-end (covered only at smoke level)
- Per-pane window config injection

**Primary gap:** the streaming pipeline is the heart of the app and has no unit
tests — only smoke.sh confirms it works end-to-end with a live stack.
A `test_streaming.py` with mocked backends would be the highest-value addition.

---

## Files changed

| File | Change |
|---|---|
| `beigebox/backends/retry_wrapper.py` | StreamStallError + _stall_guarded + stall handling in forward_stream |
| `beigebox/config.py` | Pydantic models + _validate_config() + call in load_config() |
| `beigebox/agents/operator.py` | _chat() retry loop + wrap-up hint at max_iter-2 |
| `beigebox/web/index.html` | π OR links, vi button center, balance cards, workspace D&D |
| `beigebox/main.py` | /api/v1/openrouter/balance + /api/v1/workspace/upload endpoints |
| `docker/docker-compose.yaml` | workspace/in → tmpfs, workspace/out → bind-mount |
