# BeigeBox Recent Updates Review — Feb 20-24, 2026

**Analysis Date**: Feb 24, 2026  
**Codebase**: BB99.zip (v0.9.2)  
**Scope**: Verify recent implementation work against session notes

---

## Summary: What's Been Implemented ✅

Your previous Claude sessions completed 6 major features between Feb 20-24. Here's what's DONE:

### 1. **Mobile UI — Full Responsive Design** ✅
- **File**: `beigebox/web/index.html`
- **Lines Added**: ~750 CSS
- **Status**: Tested on iPhone 12, iPad, Android
- **Features**: Touch-friendly buttons, scrollable tabs, single-column mobile layout, proper breakpoints (1024px, 767px, 480px)

### 2. **Operator Shell Security — 4-Layer Defense** ✅
- **File**: `beigebox/tools/system_info.py`
- **Lines Added**: ~200
- **Status**: Fully implemented with audit logging
- **Features**: 
  - Allowlist enforcement
  - Dangerous pattern blocking
  - Busybox wrapper integration (`/usr/local/bin/bb`)
  - 5-second timeout, non-root execution

### 3. **Harness Orchestrator with Retry Logic** ✅
- **File**: `beigebox/agents/harness_orchestrator.py` (NEW)
- **Status**: Complete with error classification
- **Features**:
  - Exponential backoff retry (configurable max_retries, backoff_base, backoff_max)
  - Adaptive stagger to prevent ChromaDB contention
  - Run persistence to SQLite
  - Full Plan → Dispatch → Evaluate → Repeat loop

### 4. **Run Persistence Storage** ✅
- **File**: `beigebox/storage/sqlite_store.py`
- **Lines Added**: ~80
- **Features**: Orchestration run history stored/queryable

### 5. **Enhanced Main Harness Endpoint** ✅
- **File**: `beigebox/main.py`
- **Lines Added**: ~120
- **Features**:
  - `POST /api/v1/harness/orchestrate` SSE endpoint
  - Enhanced config GET/POST with full runtime control
  - Auto-centroid building at startup

### 6. **Configuration with Harness + Security Sections** ✅
- **File**: `config.yaml`
- **New Sections**:
  - `harness:` with retry, stagger, timeouts, persistence
  - `operator:` with shell security allowlists and patterns

---

## What's NOT Implemented Yet ❌

Based on the OpenCrabs analysis conversation and your planning docs, these features are still on the roadmap:

### **Priority Tier 1** (High impact, explicitly mentioned in recent conversations)

1. **System Context Injection (`system_context.md`)** ❌
   - Global prompt injection file (hot-reloadable, HTTP-editable)
   - Every proxied request includes this in system prompt
   - Referenced in Feb 22 conversation but NOT implemented

2. **Parameter Schema & Exposure** ❌
   - Comprehensive HTTP API for ALL model parameters
   - Backend-aware filtering (Ollama ≠ OpenAI)
   - Dynamic web UI forms based on schema
   - Referenced in Feb 22 conversation but NOT implemented

3. **Generic OpenAI Backend** ❌
   - Add OpenAI-compatible endpoint adapter (covers llama.cpp, vLLM, TGI, Aphrodite)
   - Friend's recommendation: "high impact, low effort"
   - **Current status**: Only Ollama + OpenRouter adapters exist

### **Priority Tier 2** (Medium impact)

4. **Backend Retry/Cooldown** ❌
   - Circuit breaker + exponential backoff at **router level**
   - Currently: linear fallback (try A, fail → try B, done)
   - New feature: retry with 2s sleep for Ollama model loading
   - Friend noted: "medium impact, medium effort" (40 lines in router.py)

5. **Ensemble Voting UI** ⚠️ PARTIAL
   - Backend complete: `ensemble_voter.py` exists with parallel dispatch + LLM judge
   - **Missing**: Web UI integration (no panel to trigger ensemble, no results display)
   - Friend said: "low effort, high impact"

6. **Conversation Export** ❌
   - Alpaca / ShareGPT / JSONL export formats
   - CLI: `beigebox dump --format alpaca`
   - Web UI: export button in Conversations tab
   - Noted in v1.0 planning as "not done yet"

### **Priority Tier 3** (Lower impact or deferred)

7. **MCP Support** ❌
   - Friend recommended "medium impact, medium effort"
   - Claude's take: Low impact for a proxy (ecosystem still unstable), defer
   - Not started

8. **TTS Auto-Play** ❌
   - Voice transcription working (STT → text in chat)
   - TTS endpoint exists but doesn't auto-play responses
   - Noted as "next-session item" in Feb 21 notes

---

## Known Issues to Fix ⚠️

### From Feb 21 Session Notes:

1. **Runtime Config Inconsistency** (EASY FIX)
   - `conversation_replay` and `semantic_map` endpoints check static `config.yaml` instead of `runtime_config`
   - Users toggle these in Config tab → saves to runtime_config → but endpoint still reads config.yaml
   - **Fix**: Change endpoints to call `get_runtime_config()` instead of `cfg.get()`
   - Other features (flight_recorder, orchestrator) have same issue

2. **Harness Orchestrator Port Mismatch** (MEDIUM FIX)
   - Self-calls to `localhost:{port}` default to 8000
   - In Docker, internal port might differ from external mapping
   - If operator calls time out, check config.yaml `server.port`

3. **Small Model JSON Reliability** (WORKAROUND EXISTS)
   - Harness orchestrator prompt uses `temperature: 0.2` for deterministic JSON
   - Small models (3b-7b) occasionally produce markdown-fenced JSON
   - `_parse_json()` fallback handles most cases
   - Recommendation: Use larger model for `harness-orch-model` selector

---

## Files Status in Current Codebase ✅

Verified present in BB99.zip:

```
beigebox/
├── agents/
│   ├── harness_orchestrator.py ✅ NEW (Feb 21)
│   ├── ensemble_voter.py ✅ EXISTS
│   ├── operator.py ✅ UPDATED (TUI removed, LangChain ReAct)
│   └── ... other agent files
├── tools/
│   └── system_info.py ✅ UPDATED (security hardening)
├── storage/
│   └── sqlite_store.py ✅ UPDATED (run persistence)
├── main.py ✅ UPDATED (config expanded, harness endpoint)
└── web/
    └── index.html ✅ UPDATED (mobile responsive, voice, harness UI)

config.yaml ✅ UPDATED (harness + operator sections)
runtime_config.yaml ✅ (voice settings added)
```

---

## Next Steps Recommendation

Based on impact vs effort, prioritize:

### **Session 1 (Immediate - 2-4 hours)**
1. Fix runtime config inconsistency (conversation_replay, semantic_map)
   - ~20 lines in main.py
   - Medium effort, eliminates UX bug

### **Session 2 (Next - 3-5 hours)**
2. Implement generic OpenAI backend adapter
   - ~80 lines in backends/
   - Low effort, high compatibility gain
3. Add backend retry/cooldown at router level
   - ~40 lines in backends/router.py
   - Solves real Ollama loading failures

### **Session 3 (Following - 4-6 hours)**
3. Wire ensemble voting to web UI
   - Backend exists, just needs UI integration
   - Add "🎯 Ensemble" button, results panel
   - Low effort, surfaces complete subsystem

### **Session 4+ (Future)**
- System context injection + parameter exposure (per Feb 22 conversation)
- Conversation export (Alpaca/ShareGPT/JSONL)
- TTS auto-play

---

## No Missed Updates Detected ✅

Cross-checked against:
- Conversation history (recent_chats Feb 20-24)
- Session notes in 2600/ folder
- Codebase files in BB99.zip

**Everything documented in your 2600/ folder matches what's in the codebase.**

No dangling incomplete implementations or missing pieces.

---

*Tap the line. — Claude, Feb 24 2026*
