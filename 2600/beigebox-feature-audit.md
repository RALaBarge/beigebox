# BeigeBox — Complete Feature & Idea Audit

*Compiled from all project conversations (Feb 19–24, 2026)*
*Purpose: Capture everything Ryan said he wanted, every idea discussed, and anything not yet implemented or captured in the README.*

---

## Status Legend

- **🔴 NOT IMPLEMENTED** — Discussed, wanted, never built
- **🟡 PARTIALLY DONE** — Started or designed but incomplete
- **🟢 IMPLEMENTED** — Built and in the codebase
- **📝 IDEA ONLY** — Mentioned in passing, not formally committed to

---

## 1. Features Ryan Explicitly Said He Wants

### 1.1 🟡 ChromaDB → Embex Migration
**Source:** Feb 22 conversation (mobile UI / ensemble voting session)
**What:** Replace ChromaDB with Embex for vector storage. Embex provides a unified API across LanceDB, Qdrant, Pinecone, Milvus, and PgVector with better type safety, Pydantic models, built-in migration tools, and Rust-core performance.
**Status:** Full migration plan was written. Estimated 4-6 hours. Never started.
**Ryan's words:** Asked about it, Claude recommended it, Ryan agreed it was "medium effort, high payoff."
**What's needed:** Create EmbexVectorStore wrapper, update initialization in main.py, minimal changes to existing search/store API calls.

### 1.2 🟡 system_context.md — Global Prompt Injection
**Source:** Feb 22 (OpenCrabs analysis session)
**What:** A `system_context.md` file in BeigeBox's data dir that gets prepended to every proxied request's system prompt. Hot-reloaded on each request (no restart). Editable via HTTP API and the web UI Config tab.
**Status:** A 2600 design doc was written. Implementation was started in a later session (system context read/write endpoints exist in smoke.sh tests for v0.9.9), but needs verification that the full injection pipeline works end-to-end.
**Ryan's words:** "Yeah something that can be edited in http too"

### 1.3 🟡 Comprehensive Parameter Exposure
**Source:** Feb 22 (OpenCrabs analysis session)
**What:** Every possible tunable parameter exposed via HTTP API and dynamically-rendered web UI forms. Includes generation settings (temperature, top_p, top_k, repeat_penalty, seed, etc.), BeigeBox-specific routing params (priority weights, ensemble eligibility), and backend-aware filtering (Ollama vs OpenAI accept different params). Schema-driven approach where the web UI renders forms from parameter definitions rather than hardcoded fields.
**Status:** 2600 design doc written. Generation params partially implemented (gen params set/reset in smoke.sh for v0.9.9). Full schema-driven dynamic UI not built.
**Ryan's words:** "Can we get _every_ possible model customizable options exposed to edit?"

### 1.4 🔴 Plugin System for Custom Tools
**Source:** Multiple conversations (Feb 20 v0.8 roadmap, competitive analysis)
**What:** A proper plugin system beyond the existing hooks system. Allow users to add custom tools that the operator agent can use, without modifying core code.
**Status:** Listed on README roadmap as future. Never designed or implemented.
**Notes:** The hooks system exists but is pre/post-request only. This would be a tool registry extension.

### 1.5 🟢 Voice Pipeline (Whisper STT + Kokoro TTS)
**Source:** Feb 21 (extensive voice discussion), Feb 21 (TSS SST implementation)
**What:** Push-to-talk voice I/O. Whisper for STT, Kokoro for TTS. Mic button in web UI, lazy-loaded JS, disabled by default. Hotkey configurable. Both STT and TTS traffic goes through wiretap.
**Status:** IMPLEMENTED in v0.9.x sessions. Mic button, push-to-talk, TTS autoplay, test button all built. Docker compose profiles include whisper and kokoro services.
**Ryan's specific requests:**
- Push-to-talk only (no open mic / VAD) ✅
- Disabled by default ✅
- JS only loaded when enabled ✅
- Hotkey support ✅
- Copy Open WebUI's standards where possible ✅
- Config option like vi settings ✅

### 1.6 🟢 Conversation Export to Fine-Tuning Formats
**Source:** Multiple conversations (Feb 20 roadmap, Feb 21 v1.0 planning)
**What:** Export conversations as JSONL, Alpaca, and ShareGPT formats for fine-tuning.
**Status:** IMPLEMENTED — export endpoints and UI exist in v0.9.9, tested in smoke.sh (3 formats + bad format 400).

### 1.7 🟢 Ensemble Voting / Multi-Model Comparison
**Source:** Feb 22 (friend's recommendations), Feb 22 (mobile UI session)
**What:** Send same prompt to multiple models, have a judge LLM evaluate and pick the best response. Web UI with real-time streaming results and winner highlighting.
**Status:** IMPLEMENTED — EnsembleVoter class, POST /api/v1/ensemble endpoint, UI integration with "🎯 Ensemble" button.

### 1.8 🟢 Generic OpenAI Backend Support
**Source:** Feb 23 (friend's code review feedback)
**What:** Support any OpenAI-compatible backend (llama.cpp, vLLM, TGI, Aphrodite) not just Ollama. Minor health check modifications.
**Status:** IMPLEMENTED — generic backend type added to router.

### 1.9 🟢 Backend Retry / Cooldown Logic
**Source:** Feb 23 (competitive analysis, friend's feedback)
**What:** Exponential backoff for transient errors (Ollama model loading, timeouts). Circuit breaker pattern.
**Status:** IMPLEMENTED — retry wrapper with exponential backoff added.

### 1.10 🟢 Mobile Web UI
**Source:** Feb 22 (mobile UI session), Feb 24 (security/mobile completion)
**What:** Responsive CSS for tablet and mobile. Touch-friendly 44px minimum targets, hamburger menu, scrollable tabs, single-column layouts, stacked chat panes.
**Status:** IMPLEMENTED — ~750 lines of responsive CSS with breakpoints at 1024px, 767px, 480px.

### 1.11 🟢 Operator Shell Security Hardening
**Source:** Feb 20 (v0.7 session), Feb 21 (security audit), Feb 24 (4-layer defense)
**What:** 4-layer defense: allowlist enforcement, pattern blocking, audit logging, busybox wrapper. Non-root container user.
**Status:** IMPLEMENTED — busybox wrapper at /usr/local/bin/bb, comprehensive blocked patterns, audit logging with timestamps.

### 1.12 🟢 Harness Orchestrator with Retry
**Source:** Feb 21 (multi-agent session), Feb 24 (retry logic)
**What:** Goal-directed multi-agent coordinator with plan-dispatch-evaluate loop. Retry logic with exponential backoff, error classification, adaptive stagger timing.
**Status:** IMPLEMENTED — HarnessOrchestrator class with retry, run persistence, replay endpoints.

---

## 2. Ideas Discussed But Not Committed To

### 2.1 📝 LXD Containerization for Agent Isolation
**Source:** Feb 24 (mobile & security session)
**What:** Ryan asked about LXD containerization for additional isolation of the operator agent beyond the busybox hardening.
**Decision:** Claude said "not necessary" — the allowlist + busybox approach is sufficient for a local proxy. LXD was deemed overkill.
**Status:** Discussed and explicitly deferred.

### 2.2 📝 Port 2600 as Secondary Web UI
**Source:** Feb 20 (v0.6 planning session)
**What:** Optional secondary port `2600` for the web UI as a phreaker nostalgia feature.
**Status:** Mentioned as a "nice-to-have" by Claude. Ryan did not commit to it.

### 2.3 📝 Chroot / Container Sandbox for Operator
**Source:** Feb 20 (v0.6 planning)
**What:** Full chroot or container-based sandboxing for operator shell commands.
**Decision:** Explicitly deferred. Allowlist approach chosen instead.

### 2.4 📝 Interactive Web Visualization for Semantic Map (D3.js/Cytoscape)
**Source:** Feb 20 (v0.6 design docs)
**What:** Replace ASCII semantic map with interactive D3.js or Cytoscape graph visualization in the web UI.
**Status:** Listed as "future enhancement" in the semantic map design doc. Never built.

### 2.5 📝 Topic Naming via LLM
**Source:** Feb 20 (semantic map design doc)
**What:** Use an LLM to generate human-readable names for topic clusters in the semantic map.
**Status:** Listed as future enhancement. Never built.

### 2.6 📝 Semantic Map Evolution Over Time
**Source:** Feb 20 (semantic map design doc)
**What:** Track how the topic map changes as a conversation progresses.
**Status:** Listed as future enhancement. Never built.

### 2.7 📝 Conversation Comparison
**Source:** Feb 20 (semantic map design doc)
**What:** Compare topic maps across different conversations.
**Status:** Listed as future enhancement. Never built.

### 2.8 📝 Anomaly Detection in Topics
**Source:** Feb 20 (semantic map design doc)
**What:** Detect topics that don't fit any cluster — potential "interesting" or off-topic messages.
**Status:** Listed as future enhancement. Never built.

### 2.9 📝 Wake Word / Always-On Listening
**Source:** Feb 21 (voice discussion)
**What:** openWakeWord or Porcupine for ambient "hey beigebox" activation.
**Decision:** Ryan agreed this requires dedicated hardware attention and was out of scope. Push-to-talk only.

### 2.10 📝 VAD (Voice Activity Detection) for Open Mic
**Source:** Feb 21 (voice discussion, Open WebUI comparison)
**What:** @ricky0123/vad-web for automatic speech start/end detection.
**Decision:** Deferred. Push-to-talk chosen as first implementation.

### 2.11 📝 System-Wide Push-to-Talk Hotkey (Native Helper)
**Source:** Feb 21 (voice discussion)
**What:** A small native tray app or systemd service watching /dev/input for global hotkeys that work even when browser isn't focused.
**Status:** Discussed as a possibility. Not committed to.

### 2.12 📝 FTS5 as Lightweight Fallback Search
**Source:** Feb 22 (OpenCrabs analysis)
**What:** SQLite FTS5 full-text search as complement to vector search. Keyword-exact vs semantic.
**Status:** Mentioned as worth keeping in mind. Not planned.

### 2.13 📝 MCP (Model Context Protocol) Support
**Source:** Feb 23 (friend's recommendations)
**What:** MCP support for tool integration.
**Decision:** Claude actively recommended AGAINST this, calling it "high effort, low impact for a proxy system." Ryan agreed to skip.

---

## 3. Known Gaps / Incomplete Items

### 3.1 🟡 Tap Filter Server-Side Persistence
**Source:** Feb 20 (v0.8 session), Feb 21 (v0.9 planning)
**What:** Web UI tap filters reset on page reload. Save to localStorage or runtime_config for persistence across sessions/deployments.
**Status:** localStorage persistence was added. Server-side persistence for deployments without localStorage was listed as future but never built.
**README status:** Listed in v1.0 roadmap.

### 3.2 🟡 Test Coverage Gaps
**Source:** Feb 20 (v0.8 session notes), Feb 21 (v0.9 planning)
**What:** Missing tests for: `fork_conversation`, `get_model_performance`, `/api/v1/tap`, prompt injection hook, streaming cost sentinel. Tests were added later (test_v08.py with 30 tests), but coverage for newer features (harness orchestrator, ensemble voting, voice, mobile UI, system context, gen params) may be incomplete.
**Status:** Test suite has grown significantly but completeness is unverified for latest features.

### 3.3 🔴 Database Migration Tooling
**Source:** Feb 20 (v0.6 planning — "migration script in v1.0 if needed")
**What:** Proper database migration tooling for schema evolution across versions.
**Status:** Listed on README roadmap. Not designed or implemented. Currently using ALTER TABLE with NULL defaults.

### 3.4 🟡 Streaming Latency — Time-to-First-Token
**Source:** Feb 20 (v0.8 session notes)
**What:** For streaming responses, record time-to-first-token and total stream duration separately. Currently only wall-clock latency is stored.
**Status:** Streaming latency tracking was added (wall-clock, stored per response). Whether TTFT is captured separately is unclear.

### 3.5 🔴 Custom SQL Fields Usage
**Source:** Feb 20 (v0.6 planning)
**What:** `custom_field_1` and `custom_field_2` were added to the messages table for Ryan's future use. Purpose was explicitly deferred.
**Ryan's words:** "Leave blank for now, we can discuss what they're for after v0.6 implementation."
**Status:** Fields exist in schema. Never discussed further. Never used.

### 3.6 🟡 Docker Init Container Naming
**Source:** Feb 24 (latest debugging session)
**What:** Ryan noted "Change name of init docker to something understandable (its the models)" — the ollama-init container that pulls models on first start should have a clearer name.
**Status:** Noted in the latest session, not yet changed.

### 3.7 ✅ FIXED — Web UI Test Failures (was 11 errors)
**Source:** Feb 24 (debugging session)
**What:** 11 errors in test_web_ui.py — all TypeError: object NoneType can't be used in 'await' expression. Plus a missing route decorator for toggle-vi-mode.
**Status:** RESOLVED — fixture mocks made async-safe, missing `@app.post` decorator added. 23/23 passing.

### 3.8 🟡 `beigebox ring` and `beigebox sweep` CLI Commands
**Source:** Feb 21 (v1.0 planning notes left by Claude)
**What:** `ring` (ping the instance) and `sweep` (semantic search CLI) need verification that they work with the current endpoint layout.
**Status:** Flagged for testing, not confirmed fixed.

---

## 4. Architectural Ideas Not Yet Captured in README

### 4.1 Conditional Routing Workflows
**Source:** Feb 23 (competitive analysis)
**What:** Inspired by LangGraph — route based on intermediate results. E.g., if first model returns low-confidence answer, escalate to a larger model.
**Status:** Discussed conceptually. Not designed or implemented.

### 4.2 Iterative Refinement Loops
**Source:** Feb 23 (competitive analysis)
**What:** Send output back through a critic model, iterate until quality threshold met.
**Status:** Discussed conceptually. The harness orchestrator does something similar with its evaluate step, but not as a general-purpose pipeline.

### 4.3 Supervisor Delegation Pattern
**Source:** Feb 23 (competitive analysis)
**What:** More sophisticated multi-agent patterns where a supervisor dynamically assigns and reassigns tasks.
**Status:** The orchestrator does basic delegation. Full supervisor pattern not implemented.

### 4.4 Diverse Panel Synthesis for Ensemble
**Source:** Feb 23 (competitive analysis)
**What:** For open-ended questions, instead of picking a winner, synthesize the best parts of multiple model responses into a combined answer.
**Status:** Discussed. Current ensemble does winner-picking only.

### 4.5 Streaming TTS (Sentence-by-Sentence)
**Source:** Feb 21 (voice discussion)
**What:** Stream TTS output as the LLM generates text, sentence by sentence, to reduce perceived latency.
**Status:** Explicitly deferred. Simple "complete response → TTS" flow implemented first.
**Claude's note:** "Non-trivial plumbing change. Don't try in first pass."

### 4.6 File Transfer Through Proxy
**Source:** Feb 21 (comprehensive codebase review)
**What:** Base64 encoding for chat endpoints, multipart form-data for harness endpoints. Explicit routing and observability for file transfers.
**Status:** A comprehensive design document was created in 2600/. The catch-all passthrough already supports multi-modal requests transparently, but explicit routing/observability was not built.

---

## 5. Quality / Polish Items Mentioned

### 5.1 🔴 Comprehensive Documentation
**Source:** Multiple conversations
**What:** Proper user-facing documentation beyond the README.
**Status:** Listed on README roadmap for v1.0. Not started.

### 5.2 🔴 Type Hints Throughout Codebase
**Source:** Feb 20 (v0.6 planning — listed as v0.8-0.9 scope)
**What:** Add comprehensive type hints across all modules.
**Status:** Listed on early roadmaps, dropped from later ones. Not done.

### 5.3 📝 FastAPI Best Practices Adoption
**Source:** Feb 21 (codebase review)
**What:** Recommendations for adopting FastAPI best practices more fully.
**Status:** Noted in a 2600 document. Specific items unclear.

### 5.4 📝 Magic Numbers → Config
**Source:** Feb 20 (v0.5 analysis)
**What:** Move magic numbers (like `// 4` for token estimation, `0.3` for similarity threshold) into config.yaml.
**Status:** Flagged as LOW priority. Some may have been addressed; not systematically verified.

---

## 6. Summary: What's NOT in the README That Should Be

Based on reviewing the current README roadmap (as of the v0.9.9 session), these discussed items are either missing or under-represented:

1. **Embex migration** — Not mentioned in roadmap
2. **system_context.md** — Partially implemented but not documented in README features
3. **Full parameter exposure (schema-driven UI)** — Not in roadmap
4. **Plugin system** — Was in older roadmaps, may have been dropped
5. **Tap filter server-side persistence** — Was in roadmap, may still be there
6. **Database migration tooling** — Was in v1.0 roadmap
7. **Type hints** — Was in older roadmaps, dropped
8. **Comprehensive documentation** — Was in v1.0 roadmap
9. **Custom SQL fields** — Exist but undocumented
10. **File transfer design** — Design doc exists, feature not built or documented
11. **Conditional routing / iterative refinement** — Advanced patterns discussed, not documented
12. **Streaming TTS** — Discussed as future optimization, not documented
13. **D3.js semantic map visualization** — Discussed, not documented
14. **Diverse panel synthesis for ensemble** — Discussed, not documented

---

---

## 7. Decisions Made — Feb 24, 2026 (Tablet Session)

### 7.1 🔪 KILL: Semantic Conversation Map
**Reason:** "Cool viz, but mainly aesthetic" — rated Tier 2 from day one. Answers a question nobody asks in practice. Conversation Replay already shows the decision flow, which is what matters when debugging. Kill the module entirely.

### 7.2 🔪 KILL: Flight Recorder (as standalone feature)
**Reason:** Redundant with Wire Tap. Three observability features (Wire Tap, Flight Recorder, Conversation Replay) confuse new users. Flight Recorder's per-stage timing data should be MERGED into Wire Tap entries as expandable detail on each entry. This collapses three concepts into two with clear roles: Wire Tap = traffic, Replay = conversations.

### 7.3 ✅ COMPLETED: test_web_ui.py Fixes (11 errors → 0)
**Root causes found and fixed:**
- `client` fixture used bare `patch()` for async functions — Python's auto-detection of `AsyncMock` failed due to import ordering. Lifespan `await`ed `_preload_embedding_model(cfg)` against a regular `MagicMock`, causing `TypeError: object NoneType can't be used in 'await' expression`. Fixed with explicit `AsyncMock(return_value=None)`.
- `Proxy` constructor was not mocked — real `__init__` ran against mock arguments. Added `patch("beigebox.main.Proxy")`.
- Embedding classifier mock had `ready=False`, triggering unnecessary background centroid build task against mocks. Set `ready=True`.
- `toggle_vi_mode()` function at line 1409 in main.py had **no `@app.post()` decorator** — the route was never registered, so 5 toggle tests hit the catch-all passthrough. Added `@app.post("/api/v1/web-ui/toggle-vi-mode")`.

### 7.4 Priority Rankings (agreed upon)

**Top 2 Features to Add (most useful):**
1. **system_context.md** — hot-reloadable global prompt injection. One file controls behavior of every model through every frontend. Partially implemented, needs end-to-end verification.
2. **Full Generation Parameter Exposure** — schema-driven dynamic UI rendering of all tunable params with backend-aware filtering. Makes BeigeBox the single control plane for the entire stack.

**Top 2 Fixes to Add (most useful):**
1. ~~test_web_ui.py errors~~ ✅ DONE — was 11 errors, now 23/23 passing
2. **Broken web UI modal / ensemble button** — first thing new users see at localhost:1337. Non-functional buttons = "this software doesn't work" first impression. Still needs fixing.

### 7.5 Action Items
- Remove semantic_map.py module and related endpoints/tests
- Remove Flight Recorder as standalone tab/concept
- Add stage timing breakdown fields to wiretap JSONL entries
- Update Wire Tap UI to show expandable timing detail per entry
- Remove flight recorder tab from web UI
- Fix broken web UI modal / ensemble button
- Complete system_context.md end-to-end pipeline
- Complete schema-driven parameter exposure UI
- Update README, smoke tests, and test suite accordingly

---

*This audit covers every conversation in the BeigeBox project from Feb 19–24, 2026. If there were earlier conversations outside the project scope, those are not included.*
