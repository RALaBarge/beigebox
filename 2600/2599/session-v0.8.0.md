# Session Archive — v0.8.0 (Feb 20 2026, evening)

## What was built this session

Starting point was a clean v0.7.0 codebase. Everything below was designed and implemented in one session.

---

### 1. `shell_binary` fix — `beigebox/tools/system_info.py`

**Problem:** `operator.shell_binary` existed in `config.docker.yaml` and was documented, but `system_info.py` called `subprocess.run(..., shell=True)` and never read the config key. Busybox hardening only applied at the Docker build level, not at Python dispatch.

**Fix:** Added `_get_shell()` which reads `cfg["operator"]["shell_binary"]` at first call and caches it with a `/bin/sh` fallback. `_run()` now calls `[shell, "-c", cmd]` as an explicit list.

---

### 2. Streaming cost tracking — `beigebox/backends/openrouter.py`, `beigebox/proxy.py`

**Problem:** Costs were only recorded on non-streaming responses. Since almost all real usage is streaming, the cost story was fundamentally broken.

**Solution:**
- `openrouter.py`: Injects `"stream_options": {"include_usage": True}` on all streaming requests. Parses every chunk for `usage.cost`. After the stream ends, yields a sentinel line `__bb_cost__:<float>` — never forwarded to the client.
- `proxy.py` streaming path: Imports `_COST_SENTINEL_PREFIX`, intercepts sentinel lines before `yield`, passes `cost_usd=stream_cost_usd` to `_log_response`. Wire logs the streaming cost the same way non-streaming did.

---

### 3. Web dashboard charts — `beigebox/web/index.html`

Dashboard now fetches `/api/v1/costs?days=30` alongside existing calls.

- **Spend by Day** — 30-day bar chart, fills missing days with 0, labels every 7th tick. Pure Canvas 2D, no external deps.
- **Spend by Model** — Horizontal bar chart, up to 10 models sorted by cost, shows `$0.0000 · Nmsg` after each bar.
- Both charts pull CSS variables at render time so they respect the active palette theme.
- Disabled state shows config instructions rather than empty UI.

---

### 4. Conversation forking — `beigebox/storage/sqlite_store.py`, `beigebox/main.py`, `beigebox/web/index.html`

**`sqlite_store.py`:** New `fork_conversation(source_conv_id, new_conv_id, branch_at=None)` method. Copies messages 0..N into a fresh conversation ID with new message UUIDs. Returns count copied.

**`main.py`:** New `POST /api/v1/conversation/{conv_id}/fork` endpoint. Body: `{"branch_at": N}` (optional — omit for full copy). Returns `new_conversation_id`, `messages_copied`, `source_conversation`, `branch_at`.

**`index.html`:** Each message row in the replay view has a per-row `⑂` fork button. Top-level `⑂ Fork` button forks the full thread. Success banner shows new conversation ID inline.

---

### 5. Tap filters — `beigebox/main.py`, `beigebox/web/index.html`

**`main.py`:** New `GET /api/v1/tap?n=50&role=user&dir=inbound` endpoint. Reads `wire.jsonl` with server-side filtering by role and direction. Returns up to 500 entries.

**`index.html`:** New Tap tab (key `5`). Toolbar: role dropdown, direction dropdown, line count input, live checkbox (polls every 2 seconds). Entries rendered with role-coloured headers matching TUI palette. Operator → 6, Config → 7.

---

### 6. Model performance dashboard — `beigebox/storage/sqlite_store.py`, `beigebox/proxy.py`, `beigebox/main.py`, `beigebox/web/index.html`

**Schema:** Added `latency_ms REAL DEFAULT NULL` column to `messages` table. Migration added to `MIGRATIONS` list — safe to re-run on existing DBs.

**`proxy.py`:** `_log_response()` now accepts `latency_ms`. In the non-streaming path, `response.latency_ms` from the backend router is passed through.

**`sqlite_store.py`:** New `get_model_performance(days=30)` method. Returns avg/p50/p95 latency, request count, avg tokens, total cost per model. p50/p95 computed by fetching sorted latency values per model and indexing.

**`main.py`:** New `GET /api/v1/model-performance?days=30` endpoint.

**`index.html`:** Dashboard fetches `/api/v1/model-performance` alongside other calls. Renders a dual-bar horizontal chart (cyan=avg, yellow=p95 ghost behind it) plus a stats table with p95 colour-coded green/yellow/red by threshold.

---

### 7. Prompt injection detection — `hooks/prompt_injection.py`, `beigebox/proxy.py`

**`hooks/prompt_injection.py`:** Pre-request hook with seven weighted pattern families:
- `boundary_injection` — "ignore all previous instructions" variants (weight 3)
- `role_override` — "you are now / pretend to be / act as" (weight 2)
- `jailbreak_persona` — DAN, STAN, developer mode, unrestricted mode (weight 3)
- `prompt_extraction` — "repeat your system prompt" variants (weight 2)
- `delimiter_injection` — `</system>`, `[INST]`, `### Human:` etc. (weight 2)
- `encoded_payload` — base64/hex decode + instruction/execute (weight 2)
- `prompt_chaining` — "new task:", "COMMAND:", "SYSTEM:" (weight 1)

Two modes: `flag` (annotates `_bb_injection_flag`, logs to wire, passes through) and `block` (sets `_beigebox_block`, halts pipeline).

**`proxy.py`:** Added `_beigebox_block` check in both streaming and non-streaming paths, immediately after pre-hooks run. Block returns a canned refusal and logs to wiretap. Flight recorder records the block event. The streaming path yields the refusal as a proper SSE chunk then `[DONE]`.

Config:
```yaml
hooks:
  - name: prompt_injection
    path: ./hooks/prompt_injection.py
    enabled: true
    mode: flag
    score_threshold: 2
```

---

### 8. Version bump and README

- Version bumped to `0.8.0`
- README completely rewritten: removed stale "Next" items (all now done), tightened architecture section, removed redundant content moved here, updated API endpoint table, updated project structure tree, wrote new v0.9 priorities and future sections
- `2600/todo.md` content is now superseded — the Kimi review items are all addressed; the file is kept as historical record

---

## Files changed this session

| File | Change |
|---|---|
| `beigebox/tools/system_info.py` | `shell_binary` config key honoured |
| `beigebox/backends/openrouter.py` | Streaming cost capture via `include_usage` sentinel |
| `beigebox/proxy.py` | Streaming cost wired; `latency_ms` stored; `_beigebox_block` pipeline |
| `beigebox/storage/sqlite_store.py` | `latency_ms` column; `get_model_performance()`; `fork_conversation()` |
| `beigebox/main.py` | `/api/v1/tap`, `/api/v1/model-performance`, `/api/v1/conversation/{id}/fork` |
| `beigebox/web/index.html` | Cost charts, latency chart, Tap tab, fork buttons, tab renumbering |
| `hooks/prompt_injection.py` | New file — prompt injection detection hook |
| `README.md` | Full rewrite for v0.8.0 |

---

## Test gaps to close before v0.9

These were implemented but not yet covered by tests:

- `fork_conversation()` in sqlite_store
- `get_model_performance()` in sqlite_store
- `GET /api/v1/tap` endpoint (filter combinations)
- `POST /api/v1/conversation/{id}/fork` endpoint
- Prompt injection hook — pattern matching, flag vs block modes
- Streaming cost sentinel parsing in proxy
- `_beigebox_block` pipeline short-circuit (streaming + non-streaming)

Suggested approach: add `tests/test_v08.py` covering all of the above. Most can be done without chromadb.

---

## Notes for next session

**Start here:** Upload all project files as zip, read this file for context.

**Suggested priorities:**

1. **Write `tests/test_v08.py`** — cover all the gaps listed above. This is the most valuable next thing before any new features. The codebase is getting complex enough that regressions are a real risk.

2. **Streaming latency tracking** — `latency_ms` is NULL for all streaming responses. Options: time-to-first-token (measure from request start to first `delta.content` yielded), or total stream duration (measure from request start to `[DONE]`). Total duration is simpler and more useful for cost-per-second math. Store in the same `latency_ms` column with a note in the query that it's wall-clock not backend-reported for streams.

3. **Conversation search UX** — Current semantic search returns individual messages. Better: group by conversation, rank by best message score, show excerpt. Requires a two-pass approach in `vector_store.py` + a new API shape.

4. **`beigebox flash` cost summary** — The CLI command exists but doesn't yet hit `/api/v1/costs` for a formatted breakdown. Should show: total 30-day spend, spend by model table, daily average. Should also show model performance p95 latency per model.

5. **Test the Docker build end-to-end** — The busybox Dockerfile changes haven't been smoke-tested since the `shell_binary` fix. Run `docker/smoke.sh` and verify `system_info` tool routes through `bb`.
