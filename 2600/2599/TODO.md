# ✅ COMPLETE — Implemented and archived (pre-2026-03-16)

# BeigeBox — TODO / Planned Work

Roughly ordered by impact. Nothing here is broken — these are enhancements.

---

## WASM Pipeline

- [x] **Streaming WASM (buffer + re-stream)** — `_wasm_buffer_mode` flag in proxy suppresses
  per-chunk yields; WASM transform runs on assembled text; re-emitted as single SSE chunk
  to client. Always active when a WASM module is selected (non-optional by design).

- [x] **More WASM modules** — `pii_redactor`, `json_extractor`, `markdown_stripper`.
  The `opener_strip` module in `wasm_modules/opener_strip/` is the reference pattern.

- [x] **WASM module hot-reload** — `POST /api/v1/wasm/reload` reloads from disk
  without restart; `WasmRuntime.reload()` re-reads config and clears loaded dict.

- [x] **WASM config in web UI** — Config tab WASM Transforms section: loaded module
  chips, full module table with status dots, default_module input (runtime_config-backed),
  Reload button. `wasm_default_module` live-applies without restart.

---

## Observability & Latency (from `2600/loggingsuggestions.md`)

- [x] **Store TTFT in SQLite** — `ttft_ms` column added via migration; proxy passes it
  from `_stages["ttft_ms"]`; surfaced in `/api/v1/model-performance`.

- [x] **Percentile metrics (P90, P99)** — `model-performance` now returns P50/P90/P95/P99;
  dashboard table and latency chart updated.

- [x] **Latency-aware routing** — `LatencyTracker` rolling window (100 samples) per backend;
  two-pass routing skips degraded backends in first pass; `latency_p95_threshold_ms`
  per-backend config key; rolling P95 + degraded status in `/api/v1/backends` + Dashboard.

- [x] **Tokens/sec from TTFT** — now uses `tokens / ((latency_ms - ttft_ms) / 1000)` when
  `ttft_ms` is available; falls back to total latency for older rows.

- [ ] **Concurrency / load test harness** — simulate N concurrent requests, measure
  latency degradation curve, output a latency-vs-concurrency report. (deferred)

- [ ] **Replay-based benchmarking** — replay identical request sets across multiple
  models, produce side-by-side latency and output comparison. (deferred)

---

## Agent Workspace

- [x] **Workspace file listing in web UI** — Dashboard Workspace section with IN/OUT panels,
  delete buttons on OUT files, size totals with warning bar.

- [x] **Zip inspector tool** — `plugins/zip_inspector.py`; reads `.zip` from `workspace/in/`,
  returns file tree + UTF-8 text previews (capped at 8000 chars).

- [x] **Workspace size cap** — `workspace.max_mb` config key; visual warning in Dashboard
  when `out/` exceeds 80% of limit.

---

## Routing

- [x] **Decision LLM WASM awareness** — `_build_routes_block()` now appends
  `[suggest wasm: <module>]` hints; `docker/config.docker.yaml` routes annotated.

- [x] **Embedding centroid rebuild API** — `POST /api/v1/build-centroids` already existed;
  added "Rebuild" button inline on the Dashboard Embedding Classifier row.

---

## Cache Layer

- [x] **Semantic cache** — `SemanticCache` in `beigebox/cache.py`; cosine similarity via
  nomic-embed-text; lookup before routing, store after response; `EmbeddingCache` dedupes
  embed calls; `ToolResultCache` for deterministic tool calls; configurable threshold/TTL.

---

## A/B Traffic Splitting

- [x] **Per-backend `traffic_split` weight** — `MultiBackendRouter._select_ab()` does
  weighted random selection among non-degraded backends when any weight is configured;
  falls back to priority order when all weights are 0. `traffic_split` exposed in
  `/api/v1/backends` stats.

---

## Auth

- [x] **Single API key middleware** — `ApiKeyMiddleware` in `main.py`; reads
  `auth.api_key` from config on every request (hot-reloadable); accepts
  `Authorization: Bearer`, `api-key` header, `?api_key=` param; exempt paths:
  `/`, `/ui`, `/beigebox/health`, `/api/v1/status`, `/web/*`; returns OpenAI-compatible 401.

---

## Operator

- [x] **Operator streaming progress** — `Operator.run_stream()` async generator yields
  `tool_call`, `tool_result`, `answer`, `error` events. New `POST /api/v1/operator/stream`
  SSE endpoint. Web UI operator tab uses it: tool calls appear as yellow `.op-tool-call`
  entries that update to green `.op-tool-result` on completion, then final answer.
  Replaces the blocking non-streaming UX where raw JSON tool calls appeared as the answer.

- [x] **Operator conversation history** — `run()` and `run_stream()` accept optional
  `history: list[dict]` prepended after system prompt, giving multi-turn context. Web UI
  maintains `_opHistory` (capped at 20 messages / 10 turns), cleared by "✕ Clear".
  `@op` in chat tab passes `pane.history` for context. Non-streaming endpoint also accepts
  `history` field for backwards compat (CLI, council, orchestrator unaffected).

---

## Other

- [x] **Requests/day chart** — `renderReqDayChart()` in dashboard, data from
  `requests_by_day` added to `get_model_performance()` in `sqlite_store.py`.

- [x] **Conversation fork button** — ⑂ button on each search result card in
  Conversations tab; `event.stopPropagation()` so it doesn't also open replay.

- [x] **Plugin z-command auto-registration** — `plugin_loader.py` auto-registers
  `PLUGIN_NAME` as a z-command alias after loading. Override with `PLUGIN_Z_ALIASES`.

- [x] **Operator shell `enabled` gate** — `system_info.py._run()` checks
  `operator.shell.enabled` before executing; returns `""` with audit log if disabled.
