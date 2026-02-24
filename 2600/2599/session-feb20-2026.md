# 2600 Archive — Code Review & Session Notes

## Kimi Code Review (Pre-v0.6)

Original code review notes from early development. Most issues below were addressed in v0.6.

### Potential Issues Identified

1. **Port Mismatch** — Server port in config vs. documentation. Resolved: README now clearly states port 8000.

2. **Async Embedding Blocking** — `store_message` using sync `_get_embedding`. Known issue, embeddings run in background thread post-response so hot path is unaffected.

3. **Missing Error Handling in Embedding Calls** — Ollama `/api/embed` errors not gracefully handled. Partially addressed with graceful failure cascading in v0.6.

4. **Session Cache TTL Not Implemented** — `_session_routes` grows unbounded. Still open — see Future roadmap in README.

5. **Shell Allowlist Bypass Risk** — Argument injection possible in operator shell tool. Known limitation, documented in operator config.

6. **ChromaDB Thread Safety** — PersistentClient not thread-safe by default. Mitigated by background embedding queue.

7. **No Health Check Endpoint** — Resolved: `/beigebox/health` added.

### Feature Suggestions (from review)

Many of these were implemented in v0.6:
- ✅ Flight Recorder Mode
- ✅ Conversation Replay
- ✅ Semantic Conversation Map
- ✅ Multi-Backend Load Balancing
- ⬜ Model Performance Dashboard
- ⬜ Tap Filters (`--only-zcommands`, `--model=code`)
- ⬜ Prompt Injection Detection
- ⬜ Conversation Forking (`z: fork`)
- ⬜ Auto-Summarization for Context Window Management
- ⬜ Plugin System for Custom Tools (hooks system partially covers this)

---

## Session Notes — Feb 20 2026

### What was accomplished this session

**TUI fixes:**
- Fixed `jack` crash (`ValueError: No Tab with id '--content-tab-config'`) caused by `TabbedContent(initial="")` in Textual 8.x. Fixed by removing the `initial` argument and letting `on_mount` handle activation.
- Added Flight Recorder TUI screen (`beigebox/tui/screens/flight.py`) — new tab (key `3`) with latency bars, per-stage timing breakdown, auto-polling every 2s. Registered in `SCREEN_REGISTRY`, CSS classes added to `main.tcss`.

**Slogan update:**
- All instances of "Tap the line. Own the conversation." → "Tap the line. Control the carrier."
- All instances of "tap the line · own the conversation" → "tap the line · control the carrier"
- Files affected: `README.md`, `cli.py`, `main.py`, `tui/app.py`, `app.py`, `docker/docker-compose.yaml`, `beigebox/web/index.html`

**Web UI (`beigebox/web/index.html`):**
- New single-file dependency-free web interface served at `/` and `/ui`
- Tabs: Dashboard, Chat (streaming), Conversations (semantic search + replay), Flight Recorder, Operator, Config
- Matches TUI lavender/dark palette exactly
- Number keys 1–6 switch tabs
- π button (bottom-right) toggles vi mode for the session

**Vi mode (`beigebox/web/vi.js`):**
- Loaded dynamically only when `web_ui_vi_mode: true` in `runtime_config.yaml`
- Zero JS present in the page when disabled — not just toggled off, literally absent
- Full implementation: normal/insert state machine, `hjkl/w/b/0$/G/gg`, `i/I/a/A/o/O`, `dd/yy/p`, `x`, `u` undo, `/n` search
- Visible `-- NORMAL --` / `-- INSERT --` mode indicator

**Config changes (`beigebox/config.py`):**
- Added `update_runtime_config(key, value)` — writes a single key to `runtime_config.yaml` and busts hot-reload cache

**API changes (`beigebox/main.py`):**
- `/api/v1/config` now includes `web_ui` block with `vi_mode` and `palette` from runtime config
- New `POST /api/v1/web-ui/toggle-vi-mode` endpoint — toggles and persists to `runtime_config.yaml`
- Static files mounted at `/web/` serving `beigebox/web/` directory

**runtime_config.yaml additions:**
```yaml
web_ui_vi_mode: false      # true = enable vi keybindings in chat/operator inputs
web_ui_palette: "default"  # "default" | "random" | "dracula" | "gruvbox" | "nord"
```

**Tests (`tests/test_web_ui.py`):**
- 23 tests: `TestUpdateRuntimeConfig` (6), `TestConfigEndpointWebUi` (4), `TestToggleViModeEndpoint` (5), `TestWebFileServing` (8)
- Endpoint tests skip gracefully when `chromadb` not installed (consistent with rest of test suite)
- All 12 non-dependency tests pass in isolation

### Test baseline
- 121 tests passing before this session
- All 121 still pass after changes
- 12 new tests passing, 11 skipping (chromadb not in CI env, pass in Docker)
