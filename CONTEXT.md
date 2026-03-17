# CONTEXT.md — BeigeBox implementation reference

Read this at conversation start to avoid grepping. CLAUDE.md has the architecture
overview; this file has the "where exactly is X" detail.

---

## main.py layout (large file — ~2700 lines)

Global singletons (module-level):
```
proxy, tool_registry, sqlite_store, vector_store, blob_store
decision_agent, hook_manager, backend_router, cost_tracker
embedding_classifier, auth_registry, mcp_server, amf_advertiser
```

Startup: `lifespan()` — initialises all singletons in this order:
  storage → tool_registry → decision_agent → hooks → embedding_classifier →
  backend_router → cost_tracker → auth_registry → MCP server (loads skills,
  passes to McpServer) → AMF advertiser → proxy

Key endpoints (in file order, approximate):
- `POST /v1/chat/completions` — main proxy passthrough
- `GET  /v1/models` — list models (parallel gather across backends)
- `GET  /api/v1/operator/runs` — list recent operator runs (**must be before `/{run_id}`**)
- `GET  /api/v1/operator/{run_id}` — get specific run
- `POST /api/v1/operator` — blocking operator call (used by @op in chat pane)
- `POST /api/v1/operator/stream` — **single-turn** operator SSE
- `POST /api/v1/harness/autonomous` — **multi-turn** operator loop (state reducer)
- `POST /api/v1/harness/ralph` — spec-driven dev loop (test-driven)
- `POST /api/v1/harness/orchestrated` — multi-pane orchestration
- `POST /api/v1/harness/ensemble` — parallel ensemble run
- `GET  /api/v1/tap` — wire events (SQLite first, JSONL fallback); params: n, source, event_type, conv_id, run_id, role
- `POST /mcp` — MCP server (JSON-RPC 2.0, streamable HTTP)
- `GET  /beigebox/stats` — metrics, decision LLM counters
- `GET/POST /api/v1/operator/notes` — persistent operator context

Helper functions in main.py:
- `_reduce_plan_state(workspace_out)` — reads plan.md, returns structured state dict
- `get_effective_backends_config()` — merges config + runtime_config for backends
- `get_storage_paths(cfg)` — returns (sqlite_path, vector_store_path)

---

## Operator subsystem (beigebox/agents/operator.py)

**Class:** `Operator`

Constructor kwargs:
```python
Operator(vector_store=vs, model_override=None, autonomous=False,
         pre_hook=False, post_hook=False, blob_store=None, sqlite_store=None)
```

Key instance attributes:
- `self._model` — resolved model name (use after construction to log which model)
- `self._system` — rendered system prompt (rebuilt on skills change)
- `self._tools` — dict of {name: tool_obj}
- `self._skills` — list of skill dicts
- `self._skills_dir` / `self._skills_fp` — for hot-reload detection
- `self._wire_db` — optional SQLiteStore for structured tap events

Key methods:
- `run(question, history)` → str — blocking single-turn run (used by `POST /api/v1/operator`)
- `run_stream(question, history)` → AsyncGenerator[dict] — SSE-friendly events (used by `/operator/stream`)
- `_wire(event_type, run_id, content, turn_id, tool_id, meta)` — fire-and-forget tap event, never raises
- `_reload_skills_if_changed()` — compares fingerprint, rebuilds system prompt
- `_resolve_backend_url(model)` — picks Ollama vs OpenRouter URL

System prompt templates (module-level strings):
- `_SYSTEM` — standard (tools + skills hint)
- `_SYSTEM_AUTONOMOUS` — autonomous mode (more aggressive, no restating)
- `_PRE_HOOK_SYSTEM` / `_POST_HOOK_SYSTEM` — hook-mode variants
- `_NO_TOOLS_SYSTEM` — fallback when no tools available

Loop protocol: model emits one of:
```json
{"thought": "...", "tool": "tool_name", "input": "..."}
{"thought": "...", "answer": "..."}
```

Skills block injection: `f"\n{skills_to_xml(self._skills)}"` — single line.
Full skill content loaded on demand via `read_skill` tool (SkillReaderTool).

---

## Skills system

**Loader:** `beigebox/agents/skill_loader.py`
- `load_skills(skills_dir)` → list[dict] — scans for `SKILL.md` recursively
- `skills_fingerprint(skills_dir)` → dict[path, mtime] — for hot-reload
- `skills_to_xml(skills)` → str — single-line hint injected into operator prompt

Skill dict keys: `name, description, path, dir, metadata`

Default path: `<repo_root>/2600/skills/` (or `config.yaml → skills.path`)

On-demand reading: `beigebox/tools/skill_reader.py` — `SkillReaderTool`
  - `read_skill('list')` → names
  - `read_skill('<name>')` → full SKILL.md body

MCP exposure: skills available via `resources/list` + `resources/read` (URI: `skill://{name}`)

---

## MCP server (beigebox/mcp_server.py)

**Class:** `McpServer(tool_registry, operator_factory=None, skills=[])`

Supported JSON-RPC methods:
- `initialize` — handshake, returns capabilities (`tools`, `resources` if skills exist)
- `tools/list` — BeigeBox tools + `operator/run` if operator enabled
- `tools/call` — run a tool; special case `operator/run`
- `resources/list` — list skills as MCP resources (`skill://{name}`)
- `resources/read` — return full SKILL.md for a skill URI

Wired up in `lifespan()`: skills loaded from config path, passed to McpServer.
operator_factory is an async closure that creates a VectorStore + Operator per call.

---

## HarnessOrchestrator (beigebox/agents/harness_orchestrator.py)

**Class:** `HarnessOrchestrator`

Constructor kwargs:
```python
HarnessOrchestrator(available_targets=None, model=None, max_rounds=8,
                    task_stagger_seconds=0.4, backend_router=None,
                    injection_queue=None, sqlite_store=None)
```

Key instance attributes:
- `self.run_id` — set at start of `run()`, hex[:16]
- `self._wire_db` — optional SQLiteStore for structured tap events

Key methods:
- `run(goal)` → AsyncGenerator[dict] — full plan→dispatch→evaluate loop; yields `start`, `plan`, `dispatch`, `result`, `evaluate`, `finish`, `injected`, `error` events
- `_wire(event_type, run_id, content, turn_id, meta)` — fire-and-forget tap event, never raises

Wire events emitted: `harness_start`, `harness_plan`, `harness_dispatch`, `harness_turn`, `harness_evaluate`, `harness_inject`, `harness_end`, `harness_error`

turn_id format: `{run_id}:r{round}` (plan/dispatch/evaluate); `{run_id}:r{round}:{task_id}` (turn results)

## Harness subsystem

Three endpoints in main.py, all return SSE streams:

| Endpoint | Purpose | Key param |
|---|---|---|
| `POST /api/v1/harness/autonomous` | Multi-turn operator loop | `max_turns` (default 5) |
| `POST /api/v1/harness/ralph` | Test-driven spec loop | `test_cmd`, `spec_path` |
| `POST /api/v1/harness/orchestrated` | Multi-pane parallel | pane configs |
| `POST /api/v1/harness/ensemble` | Parallel model fan-out | targets list |

`harness/autonomous` loop:
  Turn 0: inject plan.md-writing instruction → Turn N: `_reduce_plan_state()` →
  inject structured state (objective, steps [DONE]/[NEXT], progress) →
  stop when `##DONE##` in answer or turns exhausted.

`harness/ralph` loop:
  Load spec (PROMPT.md or inline) → run Operator → run `test_cmd` →
  if exit 0: done; else: feed test failure back for next iteration.

---

## Web UI (beigebox/web/index.html)

Single file, all JS/CSS inline. No build step. ~6500 lines.

**Tab panels** (id → mode):
- `#panel-chat` — main chat
- `#panel-operator` — operator tab (single-turn since 2026-03-13)
- `#panel-harness` — harness tab (modes: orchestrated, ensemble, ralph, agentic)
- `#panel-tap` — wiretap/log viewer (v1.3.4: source/event/run/role filters, group-by-run, meta expand)
- `#panel-settings` — settings

**Operator tab JS functions:**
- `runOp()` — submit to `POST /api/v1/operator/stream` (single-turn, no max_turns)
- `stopOp()` — abort current run
- `opClear()` — clear log
- `toggleOpHistory()` / `toggleOpNotes()` — toggle panels
- `_opHistory` — array, conversation history maintained client-side

**Harness tab JS:**
- `setHarnessMode(mode)` — switches between orchestrated/ensemble/ralph/agentic
- `harnessAgenticRun()` → `POST /api/v1/harness/autonomous` (max_turns from `#harness-agentic-turns`)
- `harnessAgenticStop()` — abort
- `harnessRalphRun()` → `POST /api/v1/harness/ralph`
- `harnessEnsembleRun()` → fan-out
- `harnessOrchRun()` → orchestrated

**Model selects** populated by `populateModelSelects()` at load.
Models list fetched from `GET /v1/models`.

**SSE event rendering:** `renderOpEvent(event, container)` — handles
`tool_call`, `tool_result`, `answer`, `error`, `turn_start`, `info`.

**Tap (wiretap) panel:**
- `loadTap()` — fetch from `/api/v1/tap` with all active filters
- `_tapSelectedConv` / `_tapSelectedRun` — highlight state; `_reapplyTapHighlights()` re-applies without reload
- `tapHighlightConv(conv)` / `tapHighlightRun(runId)` — toggle highlight; run highlight also sets run input field
- `_renderTapEntry(e)` — renders one event row with source chip, event chip (clickable to filter), conv/run chips (clickable to highlight)
- `_renderTapGrouped(entries)` — groups by run_id into collapsible accordion blocks
- Filters: `#tap-source`, `#tap-event`, `#tap-role`, `#tap-run` (debounced), `#tap-n`, `#tap-group-run`
- All filter state persisted to localStorage

---

## proxy.py — Proxy class (core pipeline)

**Class: `Proxy`**
Constructor: `sqlite, vector, decision_agent, hook_manager, embedding_classifier, tool_registry, backend_router, blob_store`

Key attributes:
- `_session_cache: dict[str, tuple[str, float]]` — conversation_id → (model, timestamp); TTL 1800s; hard cap 1000→800
- `semantic_cache: SemanticCache`
- `wire: WireLog` — structured tap (JSONL)
- `wasm_runtime: WasmRuntime`

Key methods:
- `forward_chat_completion_stream(body, raw_body)` → AsyncIterator[str] — full pipeline
- `_hybrid_route(body, zcmd, conversation_id)` → (dict, Decision|None) — 4-tier routing
- `_get_session_model(conversation_id)` / `_set_session_model(conversation_id, model)` — sticky routing
- `_process_z_command(body)` → (ZCommand, dict) — parse + strip z: prefix
- `_apply_z_command(body, zcmd)` — enforce overrides (highest priority, NOT cached)
- `_inject_tool_context(body, tool_results)` — inserts results at position -1 (just before last user msg)
- `_log_messages(conversation_id, messages, model)` — SQLite + vector store + wiretap
- `_log_response(conversation_id, content, model, cost_usd, latency_ms, ttft_ms)`
- `_extract_conversation_id(body)` → str — tries conversation_id/session_id, generates UUID if missing
- `_build_hook_context(body, conversation_id, model, decision)` → dict

Non-obvious:
- Session cache: proactive TTL sweep every ~100 writes; hard cap trim 1000→800
- z-command overrides never cached (explicit user intent)
- Tool context injected at position -1, not appended
- Embedding classifier + decision LLM run in thread executor (non-blocking)

---

## backends/router.py

**`LatencyTracker`**: rolling window (default 100 samples)
- `record(backend_name, latency_ms)` — append, evict oldest
- `p95(backend_name)` → float|None
- `is_degraded(backend_name, threshold_ms)` → bool
- Thread-safe (single asyncio event loop)

**`MultiBackendRouter`**: `__init__(backends_config: list[dict])`
- `backends: list[BaseBackend]` — sorted by priority
- `forward(body)` → BackendResponse — two-pass: fast then degraded fallback
- `forward_stream(body)` → AsyncIterator[str] — merged loop (can't retry mid-stream); yields `[DONE]` with error message on failure
- `_partition_backends(model)` → (fast, degraded) — based on P95 threshold + allowed_models glob
- `_select_ab(backends)` → list — weighted random primary + rest as fallbacks (random.choices)
- `list_all_models()` → dict — parallel gather across all backends
- `health()` → list[dict] — per-backend health + rolling stats
- Backends wrapped in `RetryableBackendWrapper` (exponential backoff, configurable max_retries)
- `_bb_force_backend` in body → direct targeting (stripped before forwarding)

---

## agents/decision.py

**`Decision` dataclass:** `model, needs_search, needs_rag, tools: list[str], reasoning, confidence: float, fallback: bool, wasm_module`
- `DEFAULT_DECISION = Decision(fallback=True)`

**`DecisionAgent`**: `__init__(model, backend_url, timeout=5, routes, available_tools, default_model, wasm_modules)`
- `enabled: bool` — True if model + backend_url configured
- `_decisions_total, _fallbacks_total` — monotonic counters (never reset)
- `decide(user_message, timeout)` → Decision — async; temp=0.1, max_tokens=256; returns DEFAULT on any failure (never blocks pipeline)
- `_parse_response(text)` → Decision — strips markdown fences; validates WASM module names; filters tool names against registry
- `preload(retries=5, base_delay=5.0)` — async; exponential backoff; Ollama keep_alive=-1; 30s startup delay to avoid race
- `fallback_stats()` → dict — decisions_total, fallbacks_total, fallback_rate
- `from_config(available_tools)` → DecisionAgent — factory

Non-obvious:
- System prompt built once at startup (routes/tools don't change at runtime)
- All failures return DEFAULT (fault-tolerant, never surfaces to client)
- WASM module validation guards against hallucinated names

---

## agents/embedding_classifier.py

**`EmbeddingDecision` dataclass:** `tier (simple/complex), confidence: float, model, latency_ms, borderline: bool`

Prototype sets (module-level): `SIMPLE_PROTOTYPES` (40), `COMPLEX_PROTOTYPES` (31), `CODE_PROTOTYPES` (15), `CREATIVE_PROTOTYPES` (15)

**`EmbeddingClassifier`**:
- `_centroids: dict[str, np.ndarray]` — route → centroid vector (mean of prototypes, L2-normalized)
- `threshold: float` — confidence margin below which `borderline=True` (default 0.04)
- `ready: bool` — True if centroids loaded
- `classify(prompt)` → EmbeddingDecision — embed + cosine similarity (np.dot of unit vectors) against all centroids; confidence = best - second_best
- `_embed(text)` → np.ndarray|None — call Ollama /api/embed; L2-normalize
- `build_centroids()` → bool — compute + save .npy files
- Singleton pattern (module-level `_singleton`) — no centroid reload per request
- Code/creative routes map to "complex" tier for backward compat

---

## agents/zcommand.py

`ZCommand` dataclass: `active, route, model, tools: list[str], tool_input, message, raw_directives, is_help, is_fork`

`parse_z_command(text)` → ZCommand:
- Matches `z:\s*(.+)` (case-insensitive)
- Left of first space = directives (comma-separated); right = user message
- `ROUTE_ALIASES`: simple/easy/fast→fast, complex/hard/large→large, code/coding→code, reason/reasoning→reason
- `TOOL_DIRECTIVES`: search→web_search, memory/rag/recall→memory, calc/math→calculator, time/date→datetime, sysinfo→system_info
- Unrecognised tokens self-heal (prepended back to message)
- `is_help=True` if "help" directive; `is_fork=True` if "fork"
- For calc: remaining text = `tool_input`
- Z-command overrides NOT cached in session (explicit user intent)

---

## hooks.py

**`HookManager`**: `__init__(hooks_dir, hook_configs)`
- Loads .py files dynamically (importlib.util); skips `__*.py`
- Hooks have `pre_request(body, context) → dict|None` and/or `post_response(body, response, context) → dict|None`
- `run_pre_request(body, context)` → dict — chain all hooks; None return = no change
- `run_post_response(body, response, context)` → dict
- Context dict: `conversation_id, model, user_message, decision, config, vector_store`
- Exceptions logged and skipped (never block pipeline)
- No hot-reload (loaded once at startup)

---

## auth.py

**`MultiKeyAuthRegistry`**: `__init__(auth_cfg)`
- `_token_map: dict[str, KeyMeta]` — token → meta
- `_rate_windows: dict[str, deque]` — rolling 60s window per key name
- `validate(token)` → KeyMeta|None
- `check_rate_limit(meta)` → bool — sliding window; evict old timestamps; record new on True
- `check_endpoint(meta, path)` → bool — fnmatch patterns (e.g. "/v1/*")
- `check_model(meta, model)` → bool — fnmatch patterns (e.g. "llama3:*")
- Token resolution: agentauth keychain → `BB_<NAME>_TOKEN` env var
- Legacy single `auth.api_key` still works (wildcard key)

**`ApiKeyMiddleware`**: FastAPI middleware; validates token on every request; 401 on failure; `/health` exempt

---

## amf_mesh.py

**`AmfMeshAdvertiser`**: mDNS (via zeroconf) + NATS heartbeat
- Service type: `_amf-agent._tcp.local.`
- TXT record: id, ep (MCP endpoint), proto, tags, trust_domain, status
- NATS events: CloudEvents v1.0 envelope (specversion, id, source, type, time, datacontenttype)
- Gracefully degrades if zeroconf or nats-py missing
- `start()` / `stop()` — async; publishes online/offline events

---

## wasm_runtime.py

**`WasmRuntime`**: `__init__(cfg)`
- Uses wasmtime-py; gracefully disabled if not installed
- Modules: load from wasm_modules/ dir; each is compiled WASI target (exports `_start`)
- `transform_response(module_name, data)` → dict — JSON → WASM → JSON; falls through unmodified on any error
- `transform_text(module_name, text)` → str — UTF-8 → WASM → UTF-8
- Timeout configurable (default 500ms); exec via ThreadPoolExecutor (2 workers)
- Temp files for stdin/stdout (wasmtime-py requires file paths); deleted in finally
- Any failure is silent (WASM never on critical path)
- `reload()` → list[str] — reload from disk (fresh config)

---

## costs.py

**`CostTracker`**: `__init__(sqlite: SQLiteStore)`
- `get_stats(days=30)` → dict — total, average_daily, by_model, by_day, by_conversation (top 20)
- `get_total()` → float — all-time sum
- Queries messages table cost_usd column

---

## cli.py — CLI commands (phreaker naming)

- `beigebox dial` — start server (uvicorn.run)
- `beigebox tap` — live tap (follow log, filters for role/raw/last_n)
- `beigebox setup` — pull models into Ollama (embedding + decision_llm + extras); stream /api/pull; timeout=None (slow connections)
- `beigebox flash` — stats dashboard
- `beigebox ring` — health check all backends
- `beigebox sweep` — semantic search over conversation history
- `beigebox dump` — export conversations to JSON
- `beigebox tone` — print banner
- `beigebox build-centroids` — build embedding classifier centroid .npy files

---

## config.py

- `get_config()` → dict — cached; load once at startup from `config.yaml`
- `get_runtime_config()` → dict — hot-reload; mtime check debounced 1s (`_RUNTIME_MTIME_CHECK_INTERVAL`)
- Env var resolution: `${VAR}` or `${VAR:-default}` in all string values (recursive)
- Pydantic validation: `extra="allow"` (unknown keys don't break); warnings only, never blocks startup
- Runtime config path: `data/runtime_config.yaml` (relative to CWD or config-specified)

---

## storage/sqlite_store.py

**Tables:**
- `conversations(id PK, created_at)`
- `messages(id PK, conversation_id FK, role, content, model, timestamp, token_count, cost_usd, latency_ms, ttft_ms, custom_field_1, custom_field_2)`
- `operator_runs(id PK, created_at, query, history JSON, model, status, result, latency_ms, updated_at)`
- `harness_runs(id PK, created_at, goal, targets JSON, model, max_rounds, final_answer, total_rounds, was_capped, total_latency_ms, error_count, events_jsonl)`
- `wire_events(id PK, ts, event_type, source, conv_id, run_id, turn_id, tool_id, model, role, content, meta TEXT JSON, misc1, misc2)` — structured tap events from all subsystems; indexes on conv_id, run_id, event_type, ts

**Key methods:**
- `ensure_conversation(conversation_id, created_at)` — INSERT OR IGNORE
- `store_message(msg, cost_usd, latency_ms, ttft_ms)` — INSERT OR REPLACE
- `get_model_performance(days=30)` — P50/90/95/99 latency, TTFT, tokens/sec, total cost per model
- `export_conversations(output_path)` — JSON export
- `log_wire_event(event_type, source, content, role, model, conv_id, run_id, turn_id, tool_id, meta, misc1, misc2)` — structured tap write; content truncated to 2k chars; has try/except, never raises
- `get_wire_events(n, event_type, source, conv_id, run_id, role)` — filtered tap query, newest-first; meta JSON auto-parsed
- WAL mode (PRAGMA journal_mode=WAL) — concurrent readers during writes
- Migrations via ALTER TABLE (append-only, safe to re-run; duplicate column errors silently swallowed)
- Tokens/sec subtracts TTFT from total latency (generation time only)

---

## storage/blob_store.py

Content-addressed gzip storage: `{blobs_dir}/{hash[:2]}/{hash}.gz`
- `write(content)` → hash — SHA-256, idempotent
- `read(hash)` → str
- `exists(hash)` → bool
- Natural dedup (same content → same hash → no-op on second write)
- Used for tool output capture and document indexing

---

## tools/registry.py

Default-enabled tools (no deps): `calculator, datetime, system_info, workspace_file`
Conditionally enabled: `web_search/google_search, web_scraper, memory, document_search`
Disabled by default: `pdf_reader, ensemble, browserbox, python` (requires extra deps/bwrap)
Auto-enabled: `connection` (if `connections:` section in config)
Plugin discovery: `plugins/` dir (auto-loaded; warns on name conflict)

---

## tools/workspace_file.py

Actions: `list, read, write, append`
- Root: `WORKSPACE_OUT` env var or `/app/workspace/out`
- Max read: 32KB (8k tokens); max write/append: 64KB
- Path traversal protection via `resolve() + relative_to(root)`
- Infers missing action from presence of `content` (→ write) or `path` (→ read) or neither (→ list)

---

## tools/memory.py

RAG search over conversation history via vector store.
- `run(query)` → str — optional query preprocessing (fast LLM extracts 3-8 keywords); returns top-N results (default 3, min_score 0.3)
- ChromaDB distance → similarity: `score = 1 - distance` (distance range [0, 2])
- Results truncated to 300 chars each

---

## tools/skill_reader.py

`run(input_str)` → str:
- `"list"` or `""` → all skill names
- Exact name match → full SKILL.md content + script listing + reference file listing
- Fuzzy substring match → if 1 match return it; if multiple, list them; if none, error

---

## Routing tiers (request classification)

```
Tier 1 — Z-commands:   z:<cmd> prefix → zcommand.py
Tier 2 — Session cache: same conversation → sticky to last backend
Tier 3 — Embedding classifier: cosine similarity → embedding_classifier.py
Tier 4 — Decision LLM: borderline cases → decision.py (30s startup delay)
Tier 5 — Backend router: MultiBackendRouter (P95 latency, A/B split, failover)
```

Decision LLM fallback rate tracked in sqlite, exposed at `/beigebox/stats`.

---

## Caches (beigebox/cache.py)

- `SemanticCache` — vector similarity cache; eviction debounced 60s; in-place filter
- `EmbeddingCache` — OrderedDict LRU, O(1) eviction
- `ToolResultCache` — OrderedDict LRU, O(1) eviction

---

## Storage

- `beigebox/storage/sqlite_store.py` — `SQLiteStore`; tables: conversations, metrics, operator_runs
- `beigebox/storage/vector_store.py` — `VectorStore` wrapping chromadb (or alt backend)
- `beigebox/storage/blob_store.py` — `BlobStore` for large binary artifacts

---

## Tools (beigebox/tools/)

Auto-registered via `ToolRegistry`. Each tool: `.run(input_str) → str`, `.description` str.

Built-in tools: `calculator`, `datetime_tool`, `google_search`, `memory`,
`skill_reader`, `system_info`, `web_scraper`, `web_search`, `workspace_file`

Plugin tools: drop `*Tool` class in `plugins/` → auto-loaded by `tools/plugin_loader.py`

---

## Config patterns

```python
cfg = get_config()          # static, read once at startup
rt = get_runtime_config()   # hot-reloaded (1s mtime debounce)

# Feature flag pattern:
enabled = rt.get("operator_enabled", cfg.get("operator", {}).get("enabled", False))
```

---

## trajectory.py — Run scoring (feature/trajectory-eval branch → main)

Pure stdlib, no I/O. Called after each autonomous run completes.

```python
score_run(query: str, events: list[dict], max_turns: int, final_answer: str) -> dict
```

Returns: `{score, flow, efficiency, quality, intent, flags: list[str], turns_used, tool_calls}`

**Scoring dimensions (0–10 each), weighted mean:**
- **Flow** ×0.30 — penalises looped (tool,input) pairs (−2 each), turns with no tool calls (−1 each)
- **Efficiency** ×0.25 — penalises ≥80% turns used (−3), coding task with no workspace_file writes (−2)
- **Quality** ×0.30 — penalises no `##DONE##` (−3), error events (−2 each), no tools + short answer (−1)
- **Intent** ×0.15 — 10 if answer shares keywords with query, 5 if empty, 7 otherwise

**Flags:** `loop_detected`, `hit_turn_cap`, `no_file_writes`

SQLite: `operator_runs.score_json TEXT` (nullable, NULL for old runs).
- `store_run_score(run_id, score)` — UPDATE WHERE id=?
- `list_operator_runs()` now includes score_json

SSE event emitted at run end: `{"type": "run_score", "score": {...}}`

UI: score badge rendered in harness agentic output + operator history panel.
Score colour: ≥8 green, ≥6 yellow, <6 red.

---

## agents/pruner.py — Adversarial context pruner (feature/context-pruning branch → main)

Runs between autonomous turns. Compresses `cur_question` to only what's needed for the next step.

```python
class ContextPruner:
    def __init__(self, model, backend_url, timeout=8)
    def prune(self, cur_question: str, next_step_name: str) -> str  # sync, httpx
    @property
    def enabled(self) -> bool
    @classmethod
    def from_config(cls) -> ContextPruner
```

- Always returns original on error/timeout — never blocks pipeline
- Config: `operator.context_pruning.{enabled, model, timeout}` in config.yaml (default off)
- Runs via `run_in_executor` (sync httpx) between turns; compresses previous-turn answer before it enters history
- Called as: `pruner.prune(final_answer, f"turn {turn_n+1}")` → pruned text replaces answer in cur_history

---

## agents/reflector.py — Background reflector (feature/temporal-layering branch → main)

Fire-and-forget per turn. Analyses turn output, queues short insight for injection into next turn.

```python
class Reflector:
    def __init__(self, model, backend_url, timeout=20)
    async def reflect_async(self, turn_answer, cur_question, step_name)  # fire-and-forget
    def consume_insight(self) -> str | None  # returns None if not done yet
    @property
    def enabled(self) -> bool
    @classmethod
    def from_config(cls) -> Reflector
```

- Uses asyncio.Task (httpx.AsyncClient, async-native)
- `consume_insight()` returns None if task not done — never blocks
- Insight injected as `[Reflection] <text>` appended to previous-turn answer in history
- Config: `operator.reflection.{enabled, model, timeout}` in config.yaml (default off)
- UI: "Reflect" checkbox in operator agent toolbar (server-side flag gates actual reflection)

---

## agents/shadow.py — Shadow agent (feature/shadow-agents branch → main)

Runs a counterfactual operator in parallel on turn 0 only. Surfaces alternative plan if it diverges.

```python
class ShadowAgent:
    def __init__(self, model, backend_url, timeout=30, max_tool_calls=3)
    async def run_shadow(self, question: str, vector_store) -> str | None
    @staticmethod
    def diverges(primary: str, shadow: str, threshold=0.3) -> bool  # Jaccard word overlap
    @property
    def enabled(self) -> bool
    @classmethod
    def from_config(cls) -> ShadowAgent
```

- Shadow gets `max_tool_calls=3` budget (vs primary's ~10). Operator now accepts `max_tool_calls: int | None`.
- Shadow prompt injects: "challenge the obvious approach — what alternative would a senior engineer prefer?"
- `diverges()`: Jaccard similarity < (1−threshold) → True. Pure stdlib.
- Runs via `asyncio.ensure_future` + `shield` + 2s `wait_for` — never blocks primary stream
- SSE event on divergence: `{"type": "alternative_plan", "content": shadow_answer}`
- Config: `harness.shadow_agents.{enabled, model, timeout, max_tool_calls, divergence_threshold}` (default off)
- UI: "shadow" checkbox in Harness Agentic toolbar; `<details>` block for alternative plan

---

## harness/autonomous — Context isolation (implemented 2026-03-13)

Every autonomous turn gets a clean subagent call — `op.run_stream(cur_question, [])`.
No history passed on any turn (including turn 0). `initial_history` kept only for sqlite run record.
Token budget: ~9K tokens/turn vs ~15K with history accumulation.

---

## Key conventions

- Operator runs are **always created fresh** per request (no shared Operator instance)
- `proxy.wire` — the Wiretap logger; guard all calls with `if _wire:`
- `_conv_id` = random hex for wiretap grouping; `_run_id` = stored in operator_runs table
- History is capped to 8 messages before passing to Operator (prevents prompt bloat)
- `autonomous=True` on Operator selects `_SYSTEM_AUTONOMOUS` prompt (more directive)
- Multi-turn context is injected via structured `cur_question` per turn — NOT via history
- New agent modules (pruner, reflector, shadow) all follow: `from_config()` factory, `enabled` property, never block pipeline, return safe default on error
- All 4 feature branches fully committed (2026-03-13): `feature/trajectory-eval`, `feature/context-pruning`, `feature/temporal-layering`, `feature/shadow-agents`
- Each branch has: module, tests/, wiring in main.py, config.yaml block, web UI affordance where applicable
