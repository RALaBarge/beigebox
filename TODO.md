# BeigeBox — TODO / Planned Work

Roughly ordered by impact. Nothing here is broken — these are enhancements.

---

## WASM Pipeline

- [x] **Streaming WASM (buffer + re-stream)** — `_wasm_buffer_mode` flag in proxy suppresses
  per-chunk yields; WASM transform runs on assembled text; re-emitted as single SSE chunk
  to client. Always active when a WASM module is selected (non-optional by design).

- [ ] **More WASM modules** — `pii_redactor`, `json_extractor`, `markdown_stripper`.
  The `opener_strip` module in `wasm_modules/opener_strip/` is the reference pattern.

- [ ] **WASM module hot-reload** — currently modules are loaded at startup.
  Add a `POST /api/v1/wasm/reload` endpoint to reload without restart.

- [ ] **WASM config in web UI** — Config tab doesn't have special handling for the
  `wasm:` section yet. Add enable/disable toggles per module.

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
  latency degradation curve, output a latency-vs-concurrency report.

- [ ] **Replay-based benchmarking** — replay identical request sets across multiple
  models, produce side-by-side latency and output comparison. Natural extension of
  the existing conversation replay system.

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

## Other

- [ ] **`2600/loggingsuggestions.md`** — review for any remaining items worth pulling in.
  The latency items above are extracted from there; the benchmark/portfolio section
  (section 8) is worth doing when the latency tracking is solid.
