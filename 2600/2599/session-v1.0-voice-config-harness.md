# Session Notes — v1.0 Voice, Config Expansion, Harness Orchestrator
**Date:** 2026-02-21  
**Claude instance:** Sonnet 4.6  
**Session scope:** Three major feature additions on top of the multi-pane/harness base built in the prior session.

---

## Hey future me —

Here's what happened this session and what you'll want to know before touching anything.

---

## What Was Built

### 1. Voice / Push-to-Talk

The other Claude instance that worked on this before me gave Ryan a minimal standalone `index.html` and a `main.py` that were basically a proof-of-concept, not integrated with the actual codebase. I cherry-picked just the JS voice logic and wired it into the real UI.

**Where it lives:**
- `index.html` — mic button in the chat input bar, hidden by default (`display:none`). Shows when `voice_enabled` in runtime config is true.
- `runtime_config.yaml` — added `voice_enabled: false` and `voice_hotkey: ""`
- `main.py` — `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/audio/translations` were already there as `_wire_and_forward` passthrough routes. No changes needed.
- `GET /api/v1/config` now returns `web_ui.voice_enabled` and `web_ui.voice_hotkey`
- Boot init (`initWebUi()`) checks the config and calls `enableVoiceUI(hotkey)` if enabled

**How the mic button works:**
- Click = toggle
- Hold (mousedown/mouseup) = push-to-talk
- Touch events wired too (mobile)
- Optional hotkey from config (keydown listener added only if hotkey is set)
- Records as `audio/webm;codecs=opus`, falls back to `audio/mp4` (Safari)
- POSTs to `/v1/audio/transcriptions` as multipart form with `model: whisper-1`
- Response `.text` field auto-fills chat input and triggers send

**TTS is not wired to auto-play responses yet.** The endpoint exists and forwards, but nothing in the UI hooks into it on the assistant response side. That's a next-session item.

---

### 2. Full Config in Web UI

Ryan wanted every config.yaml setting visible and editable from the Config tab (tab 8), not just the handful that were there before.

**What changed:**
- `GET /api/v1/config` rewritten — now returns the full merged config (config.yaml + runtime_config.yaml overrides). Grouped into sections. API keys are redacted. Every feature flag returns its sub-options with runtime overrides applied.
- `POST /api/v1/config` expanded — accepts ~30 keys now including all feature flag sub-options. All hot-applied to `runtime_config.yaml` via `update_runtime_config()`. The live-apply block at the bottom handles decision LLM toggle, default model swap, log_conversations toggle.
- The `loadConfig()` JS function was completely rewritten. It now renders full grouped sections: Backend, Server, Storage, Decision LLM, Operator, Tools, Feature Flags, Routing, Multi-Backend, Model Advertising, Logging, Web UI, Session Overrides.
- **Feature flag sub-options** — when you toggle a feature flag (e.g. Flight Recorder), an indented panel of its sub-options slides in below the toggle. Cost tracking shows track_openrouter/track_local. Orchestrator shows max_parallel, timeouts. Flight recorder shows retention hours and max records. Semantic map shows threshold and max topics. Auto-summarization shows token budget, model, keep_last.
- The save bar is sticky at the top of the Config tab scroll area.
- Build Centroids button was removed from the Config tab — it's now auto-built at startup (see below).

**A gotcha:** `POST /api/v1/config` was returning 404 for Ryan before this session because the old endpoint in his running container didn't have the expanded allowed keys list. The UI now shows a specific message: "Config save endpoint not found — rebuild the container with the latest main.py" instead of a cryptic error.

---

### 3. Auto-Build Centroids at Startup

Ryan asked how to make `build-centroids` automatic. Answer: it already had everything it needed.

In `main.py`'s `lifespan()`, after `_preload_embedding_model()` completes (so the model is warm), we check `embedding_classifier.ready`. If false, we fire `asyncio.create_task(_auto_build_centroids())`. Non-blocking — startup completes immediately, centroids build in the background. Logs show "auto-built successfully" when done.

The Build Centroids button was removed from the Config tab UI since it's no longer needed.

---

### 4. Error Handling Pass

Ryan hit a bunch of raw error boxes from disabled features. Did a systematic pass:

- `api()` helper now preserves `err.status` and `err.body` on failures
- `featureDisabledMsg(name, configKey, hint)` — yellow left-border callout with a link to the Config tab (clicking switches to tab 8)
- `apiErrorMsg(e, name, configKey, hint)` — auto-detects 404/disabled vs real error

Wired into: Flight Recorder, Flight Record detail expand, Tap, Conversation Replay, Semantic Search, Cost Tracking section on dashboard.

---

### 5. Harness Orchestrated Mode

The big one. Ryan wanted a "harness master" that takes a goal, breaks it down, hands tasks to agents, and iterates.

**New file:** `beigebox/agents/harness_orchestrator.py`

`HarnessOrchestrator` is an async generator class. Call `orch.run(goal)` and iterate the yielded events. The loop:

1. **Plan** — LLM call with system prompt explaining available targets and asking for a JSON task list OR a finish signal
2. **Dispatch** — fires all tasks in parallel with `asyncio.gather()`
3. **Evaluate** — separate LLM call asking if results are sufficient
4. Repeat or emit `finish`

Each task routes to either `_run_operator()` (HTTP POST to its own `/api/v1/operator`) or `_run_model()` (direct to backend). The operator self-call uses `localhost:{config_port}` so it picks up the correct port from config.

JSON parsing is fault-tolerant (`_parse_json`) — strips markdown fences, falls back to regex extraction.

**New endpoint:** `POST /api/v1/harness/orchestrate` — SSE stream of JSON event dicts. Frontend consumes with `ReadableStream`.

**UI changes in index.html:**
- Harness panel replaced wholesale (had to use Python for the replacement because of unicode characters in the original)
- Mode toggle buttons at top: Manual / Orchestrated
- `setHarnessMode(mode)` — shows/hides the right controls, target areas, grids
- Orchestrated mode has: master model selector, max rounds input, available-targets chip bar, the prompt textarea
- Master pane (top half, cyan border) shows live orchestrator reasoning — plan blocks with task breakdown, evaluation verdicts, final answer
- Worker panes appear dynamically in the bottom grid as tasks are dispatched, one per task per round, with round number label
- Abort via `_orchAbort = new AbortController()` — the Clear button cancels in-flight
- `loadModelsForPanes()` was extended to also populate `harness-orch-add-select` and `harness-orch-model`

**One thing to watch:** The orchestrator LLM prompt uses `temperature: 0.2` for deterministic JSON output. Small models (like llama3.2:3b) sometimes still produce markdown-fenced JSON or extra prose. The `_parse_json` fallback handles most of that. If a model is consistently failing to produce valid JSON, the fallback kicks in and returns the raw text as `reasoning` which at least doesn't crash — it just treats it as a non-parseable plan and emits an error event.

---

## State of the Codebase

**What's working:**
- All 8 tabs functional
- Config tab shows and saves everything
- Voice hidden by default, activates cleanly from Config
- Harness manual mode unchanged and working
- Harness orchestrated mode: UI is complete, backend is complete, not yet tested end-to-end by Ryan (just deployed)
- Centroid auto-build fires on startup
- Pane close (✕) actually removes the pane now (was just clearing history before)

**What's not done yet:**
- TTS auto-play on assistant responses — the endpoint is wired but nothing reads the audio stream and plays it
- The orchestrator self-calls operator via `localhost:{port}` — this works when running in Docker with port mapping, but if BeigeBox is running without Docker or on a non-standard port, verify `config.yaml server.port` matches

**Known rough edges:**
- Harness orchestrator needs a model that can reliably produce JSON. If the operator model is very small and keeps producing garbage JSON, increase `temperature` slightly (counter-intuitive but helps with variation that lands on valid structure) or point `harness-orch-model` at a larger model.
- The `conversation_replay` and `semantic_map` endpoints check `cfg.get(...)` at request time, not from runtime_config. So toggling those in the Config tab saves to runtime_config but the actual endpoint check still reads from config.yaml. The endpoint itself will return `enabled: false` until the server restarts with those set in config.yaml. This is a known inconsistency — the other feature flags (flight recorder, orchestrator) have the same issue. They're all "requires restart to fully activate" even though the UI lets you toggle them. This should be fixed by having the endpoints check runtime_config as well.
- The harness orchestrator port for self-calls defaults to 8000 from `config.get("server", {}).get("port", 8000)`. In Docker, the internal port might differ from the external mapping. If operator calls are timing out from the orchestrator, check this.

---

## Files Changed This Session

```
beigebox/agents/harness_orchestrator.py   NEW
beigebox/main.py                          config GET/POST expanded, harness endpoint added, auto-centroid startup
beigebox/web/index.html                   voice UI, config tab overhaul, harness orchestrated mode
runtime_config.yaml                       voice_enabled, voice_hotkey added
README.md                                 full rewrite for v1.0
2600/session-v1.0-voice-config-harness.md  this file
```

---

## If You're Picking This Up

1. **Check the harness orchestrator end-to-end** — Ryan was building when I left. The SSE stream, the event parsing in the UI, the worker pane creation — this is all new and untested against a live instance. There are probably UI layout issues in the master pane since the flex layout inside a panel inside a tab has some height management that might need tweaking.

2. **TTS playback** — when that comes up, the pattern is: hook into the streaming response pipeline (or post-completion), send the text to `/v1/audio/speech`, get back an audio blob, play it. The tricky part is Ollama doesn't do TTS — you need an actual TTS service (Kokoro, etc.) pointed at that endpoint. The forwarding is already there.

3. **Ensemble/voting** — was discussed at the end of a prior session. The multi-pane chat and the harness are both precursors. True ensemble would be: send same prompt to N models, collect all responses, pass them to a judge LLM, return the best or synthesize. The `HarnessOrchestrator` can almost do this already — just needs a specific prompt pattern and a single-round cap.

4. **Config endpoint inconsistency** — the feature flags that "require restart" (replay, semantic map, etc.) should be fixed to check runtime_config at request time. The pattern is already there for the ones that work (cost tracking, flight recorder check config.yaml but could be updated to also check `get_runtime_config()`).

5. **The 2600/ directory** — previous sessions documented design decisions for flight recorder, multi-backend, orchestrator, semantic map. Worth reading `session-v1.0-harness.md` for the multi-pane and original harness UI session before that one.

Good luck. Tap the line.

— Claude, Feb 21 2026
