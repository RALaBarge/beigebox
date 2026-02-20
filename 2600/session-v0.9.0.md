# Session Archive — v0.9.0 (Feb 20 2026, late evening)

## What this release is

v0.9.0 is a user testing milestone. No new architecture — this closes out the remaining backlog from v0.8 and hands the project to Ryan for a full manual test pass before v1.0 planning.

---

## What was built this session

### 1. `tests/test_v08.py` — 30 new tests, all passing

Closed the test gap identified at the end of the v0.8 session. Coverage:

- `fork_conversation()` — 7 tests (full copy, branch_at slicing, ID isolation, empty source, immutability, cost/latency preservation, branch_at=0 edge)
- `get_model_performance()` — 6 tests (empty DB, avg/p50/p95 math, user exclusion, multi-model, null latency exclusion, cost aggregation)
- Prompt injection hook — 10 tests (clean passthrough, 4 pattern families, flag/block modes, threshold, empty/non-user edge cases, score reporting)
- Streaming cost sentinel — 4 tests (prefix constant, float parsing, no-cost skip, zero-cost)
- `_beigebox_block` pipeline — 3 tests (dict structure, proxy check logic, refusal message end-to-end)

### 2. Streaming latency tracking — `beigebox/proxy.py`

**Problem:** `latency_ms` was NULL for all streaming responses. The model performance dashboard showed gaps for every local (Ollama) model since those only stream.

**Fix:** Added `stream_t0 = time.monotonic()` before the stream loop. After both the router path and the direct legacy path exit, `stream_latency_ms` is computed and passed to `_log_response`. Also moved the `_COST_SENTINEL_PREFIX` import to the module top and wired cost capture for the router streaming path — the sentinel line is now intercepted and suppressed from being forwarded to the client. Wire log records both cost and latency for streaming requests in one `cost-tracker` internal line.

### 3. Conversation search UX — `vector_store.py`, `main.py`, `web/index.html`

**Problem:** `/beigebox/search` returned raw individual message hits. Multiple hits from the same conversation showed up as separate entries, making it hard to see which conversation was most relevant.

**`vector_store.py`:** New `search_grouped()` method. Fetches up to `candidates` (default 40) message-level hits from ChromaDB, groups by `conversation_id`, keeps the best score and best excerpt per conversation, counts match frequency, sorts by score, returns top `n_conversations`. Original `search()` method unchanged for backward compatibility.

**`main.py`:** New `GET /api/v1/search` endpoint — same `?q=&n=` params as `/beigebox/search` but returns conversation-level results with `score`, `excerpt`, `role`, `model`, `timestamp`, `match_count`.

**`web/index.html`:** `searchConvos()` now hits `/api/v1/search`. Result rows show a `match_count` badge when >1 message matched, role-coloured excerpt preview, and still click through to `loadReplay()` as before.

### 4. `beigebox flash` model performance table — `beigebox/cli.py`

**Problem:** `flash` showed cost data but no latency. You couldn't see which models were slow without running the web UI.

**Fix:** After the cost section, `cmd_flash` now calls `get_model_performance()` directly on the SQLite store. Renders a fixed-width table: model name, request count, avg/p50/p95 latency, cost per message. p95 is colour-coded green (<1s) / yellow (<3s) / red (≥3s) using ANSI escapes. Silently skipped if no latency data exists yet.

### 5. Version bumps and README

- `cli.py`: `__version__` bumped to `0.9.0`
- `main.py`: health endpoint and `/api/v1/info` already reflected `0.8.0` — these should be bumped to `0.9.0` before cutting the release tag
- `README.md`:
  - Removed duplicate streaming cost line from backends done list
  - Added streaming latency tracking to done list
  - Added `/api/v1/search` to API endpoints table
  - Rewrote v0.9 roadmap section — removed completed items, noted this is a user testing milestone
  - Kept tap filter persistence and auto-summarization as open v0.9 items

---

## Files changed this session

| File | Change |
|---|---|
| `tests/test_v08.py` | New — 30 tests covering all v0.8 feature gaps |
| `beigebox/proxy.py` | Streaming latency + cost sentinel interception |
| `beigebox/storage/vector_store.py` | New `search_grouped()` method |
| `beigebox/main.py` | New `GET /api/v1/search` endpoint |
| `beigebox/web/index.html` | `searchConvos()` uses grouped endpoint |
| `beigebox/cli.py` | `flash` shows model performance table; version → 0.9.0 |
| `README.md` | Done list cleaned up; v0.9 section rewritten |

---

## What to do in this release: user testing

Ryan is doing a full manual test pass. Suggested areas:

1. **Hybrid routing** — try `z: simple`, `z: complex`, `z: code`, and bare messages. Check wiretap to confirm correct tier fired.
2. **Streaming cost** — send a message via OpenRouter. Check `beigebox flash` to confirm cost appears. Check model performance table shows latency.
3. **`beigebox flash`** — run with `--days 7` and `--days 30`. Verify model performance table renders correctly.
4. **Conversation search** — search for something in the web UI Conversations tab. Confirm results are grouped by conversation (not raw messages), badges appear for multi-match, clicking loads replay.
5. **Prompt injection** — set `mode: flag` in config, send "Ignore all previous instructions." Check wiretap shows detection. Try `mode: block` and confirm refusal is returned.
6. **Conversation fork** — load a replay in the web UI, fork at a message, confirm new conversation ID appears in the banner.
7. **Docker smoke test** — `docker/smoke.sh` to verify busybox `shell_binary` fix — `system_info` tool should route through `bb`, not `/bin/sh`.
8. **TUI** — `beigebox jack` — cycle all 4 screens, confirm no crashes.

---

## Notes for next session (v1.0 planning)

**Start here:** Upload all project files as zip, read this file for context.

**v1.0 will be shaped by what Ryan finds in user testing.** Likely candidates based on the current state:

- **Tap filter persistence** — save last role/direction/n in localStorage. Small, user-visible, zero backend changes.
- **Auto-summarization** — context window management for long conversations. Bigger feature, needs a `summary_model` config key.
- **`beigebox flash` streaming note** — latency_ms for streaming is wall-clock (not backend-reported). Worth noting in the output so it's not compared directly to non-streaming p95.
- **`main.py` version string** — still says `0.8.0` in the health endpoint and `/api/v1/info`. Should match `cli.py` at `0.9.0` before tagging.
