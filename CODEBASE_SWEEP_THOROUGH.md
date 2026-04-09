# BeigeBox Thorough Codebase Audit

**Date**: 2026-04-08  
**Agent**: Spawned through beigebox proxy (Tap-logged)  
**Scope**: Priority files from medium sweep + deep bug investigation  
**Depth**: Thorough (full file reads, caller tracing, impact analysis)

---

## 🔴 CRITICAL BUGS

### Bug 1: `cache.py:280` — AttributeError on every semantic cache hit

**File**: `/home/jinx/ai-stack/beigebox/beigebox/cache.py:280`

**Issue**: 
```python
log_cache_event(
    event_type="hit",
    cache_type="semantic",
    key=user_message[:50],
    similarity=best_sim,
    ttl_remaining=int(entry.expires_at - time.time()),  # ← AttributeError
)
```

**Root Cause**:
- `_CacheEntry` dataclass (lines 137–143) defines: `embedding`, `response`, `model`, `user_message`, `ts`
- **`expires_at` field does NOT exist**
- Only one reference to `expires_at` in entire codebase — this line
- TTL enforcement uses `entry.ts`, not a separate `expires_at` field

**Impact**: 
- Every semantic-cache hit raises `AttributeError` inside `lookup()`
- Feature is disabled by default (`features.semantic_cache: false`), so it hasn't fired in production
- **Moment anyone enables semantic_cache, every hit breaks**

**Fix** (one-line change):
```python
ttl_remaining=int(max(0, self.ttl - (time.time() - entry.ts))),
```
No dataclass change needed — TTL is computable from `ts + self.ttl`.

**Risk**: **CRITICAL** (blocks semantic_cache feature entirely)

---

### Bug 2: `config.py:248` — YAML parse errors swallowed silently

**File**: `/home/jinx/ai-stack/beigebox/beigebox/config.py:234–249`

```python
try:
    with open(_RUNTIME_CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}
    _runtime_config = data.get("runtime", {})
    _runtime_mtime = mtime
except Exception:
    pass  # Keep last good config on parse error
```

**Problem**:
1. User edits `runtime_config.yaml` live
2. Syntax error introduced (e.g., missing colon in YAML)
3. Exception caught, config silently kept at stale value
4. **User has no idea the edit was rejected** — no log, no Tap event
5. Retry loop runs silently on every request until the error is fixed

**Impact**: 
- Runtime config hot-reload feature is *silently broken* if user makes a typo
- Ops can't troubleshoot (no logging, no audit trail)
- Feature claims to be "hot-reload without restart" but doesn't signal errors

**Fix**:
```python
except yaml.YAMLError as e:
    logger.warning("runtime_config.yaml parse error (keeping last good): %s", e)
except Exception as e:
    logger.warning("runtime_config.yaml load failed: %s", e)
```

**Risk**: **HIGH** (silent failure of hot-reload feature, no observability)

---

## 🟠 HIGH-PRIORITY ISSUES

### Issue 3: `embedding_classifier.py` — Sync blocking in async context

**File**: `/home/jinx/ai-stack/beigebox/beigebox/agents/embedding_classifier.py`

**Details**:
- Lines 247–251: `_embed()` uses **sync** `httpx.post(..., timeout=30.0)`
- Line 271–275: `_embed_batch()` — same pattern
- Lines 226: `np.load()` — blocking file I/O at init (tolerable)

**Current Caller**:
- `proxy.py:618`: Classifier is called via `loop.run_in_executor(None, self.embedding_classifier.classify, user_msg)`
- So the sync call is already wrapped in a threadpool executor — **not actually blocking the event loop**

**BUT — this is a footgun:**
- If anyone ever calls `classify()` directly from async context, it will silently block the event loop for up to 30s
- **Inconsistent pattern**: `SemanticCache._get_embedding()` uses async-native `httpx.AsyncClient` (correct pattern already in codebase)
- **Timeout mismatch**: SemanticCache uses 5.0s, classifier uses 30.0s for the same model on the same backend

**Conversion Complexity**: **Small** (~20 lines of `_embed()`, no other callers)

**Recommended Fix**: 
1. Migrate `_embed()` to async (mimic `SemanticCache._get_embedding()`)
2. Change timeout from 30.0s to 5.0s (consistent with cache)
3. Update proxy.py:618 to `await self.embedding_classifier.classify(user_msg)`

**Risk**: **MEDIUM** (footgun for future refactors, but not actively breaking today)

---

### Issue 4: `main.py` — File too large (5186 lines, 96 endpoints, 60+ exception handlers)

**File**: `/home/jinx/ai-stack/beigebox/beigebox/main.py`

**Metrics**:
- **96 endpoints** (`@app.get/post/put/delete/patch`)
- **60+ exception handlers** (`except Exception:`)
- **Silent exception handlers**: ~25 blocks that catch exceptions but log nothing
  - Examples: `except Exception: _vs = None`, `except Exception: return JSONResponse(...)`
  - Result: client-side debugging impossible without `wire.jsonl` inspection

**Examples of silent exceptions**:
- Line 258: Vector store init failure → `_vs = None` (no log)
- Lines 3038, 3051, 3083, 3100: SSE streaming → `except Exception: pass` (completely silent)
- Lines 1333, 1538, 1905, ... (list of ~25 lines): 400 errors → `return JSONResponse(...)` without logging the request

**Recommended Split** (into `api/routers/` package):

| Target module | Content | Est. lines |
|---|---|---|
| `api/routers/chat.py` | `/v1/chat/completions`, `/v1/models`, `/v1/embeddings` | 300 |
| `api/routers/mcp.py` | `/mcp` (JSON-RPC) | 150 |
| `api/routers/harness.py` | `/api/v1/harness/*`, `/api/v1/ralph/*` | 900 |
| `api/routers/operator.py` | `/api/v1/operator/*` | 300 |
| `api/routers/tap.py` | `/api/v1/tap/*`, metrics | 250 |
| `api/routers/config_admin.py` | `/api/v1/config/*`, `/api/v1/backends/*` | 400 |
| `api/routers/conversations.py` | CRUD + replay | 200 |
| `api/routers/auth_oauth.py` | `/oauth/*` | 150 |
| `api/routers/council.py` | `/council/*` | 200 |
| `api/routers/dgm.py` | DGM endpoints | 150 |
| `main.py` (residual) | App factory, lifespan, middleware, state | 800 |

**Impact on other files**: Low
- `app` imported only by `__main__.py` and docker entrypoint
- Tests import `from beigebox.main import app` — unchanged if `app` stays exported
- Use FastAPI's `APIRouter`, `app.include_router()`

**Start with**: Extract `mcp.py` (most isolated) and `harness.py` (highest value).

**Risk**: **HIGH** (maintainability, observability, debugging difficulty)

---

### Issue 5: Hard-coded Constants Across Multiple Files

**Problem**: Tuning parameters are scattered in code instead of in `config.yaml`. If ops need to adjust behavior under load, they must edit source and redeploy.

**Constants to promote to config**:

| Constant | File:Line | Value | Recommended config path |
|---|---|---|---|
| Session cache hard cap | `proxy.py:173` | `1000` | `routing.session_cache.max_entries` |
| Session cache trim floor | `proxy.py:175` | `800` | `routing.session_cache.trim_to` |
| Session cache sweep interval | `proxy.py:170` | `% 100` | `routing.session_cache.sweep_every_writes` |
| Classifier embed timeout | `embedding_classifier.py:250, 274` | `30.0s` | `embedding.timeout_seconds` |
| MCP input size limit | `mcp_server.py:434` | `1_000_000` | `mcp.max_request_bytes` |
| SemanticCache eviction interval | `cache.py:186` | `60.0s` | `semantic_cache.eviction_interval_seconds` |
| Latency rolling window size | `router.py:48` | `100` | `backends.latency_window_size` |
| Runtime mtime check interval | `config.py:165` | `1.0s` | `advanced.runtime_config_check_interval` |

**Highest-value moves**:
1. MCP input limit (genuine policy setting)
2. Embedding timeout (currently 30.0 vs 5.0 inconsistency across classifier and cache)
3. Session cache caps (ops may need to tune under memory pressure)

**Risk**: **MEDIUM** (performance tuning flexibility, not a bug)

---

## 🟡 MEDIUM-PRIORITY ISSUES

### Issue 6: Observability Gaps — Silent Failures with No Tap Events

**Pattern 1 — Parse errors not logged**:
- `config.py:248` — runtime config YAML error (mentioned above)
- `main.py:1333, 1538, ...` — 400 errors from malformed JSON request bodies; errors not logged (client debugging impossible)

**Pattern 2 — Debug-level logging in production paths**:
- `cache.py:219-221` — embedding fetch failure logged at `logger.debug` (usually filtered in production)
- `embedding_classifier.py:265, 287` — embedding/centroid errors at `logger.debug`
- Result: backend outage looks like "feature just doesn't work" with no trace

**Pattern 3 — Silent exception swallows inside SSE**:
- `main.py:3038, 3051, 3083, 3100` — `except Exception: pass` inside tool execution / harness turn generators
- If a tool fails mid-stream, client sees nothing (stream just ends)

**Pattern 4 — Missing Tap events for config changes**:
- Runtime config reload **success** not logged — can't distinguish "config changed" from "nothing happened" in tap stream
- Runtime config reload **failure** not logged (mentioned above)

**Recommended fixes**:
1. Upgrade `logger.debug` → `logger.warning` on cache/classifier failures
2. Add `logger.warning()` calls to all silent `except Exception:` blocks
3. Add Tap events for config reload (both success and failure)
4. Add Tap events for SSE streaming errors

**Risk**: **MEDIUM** (observability, debugging)

---

## Summary Table

| Priority | Issue | File | Type | Fix Complexity | Impact |
|---|---|---|---|---|---|
| 🔴 CRITICAL | `expires_at` AttributeError | `cache.py:280` | Bug | 1 line | Breaks semantic_cache |
| 🔴 CRITICAL | Silent config reload error | `config.py:248` | Bug | 2 lines | Breaks hot-reload observability |
| 🟠 HIGH | Sync blocking in async | `embedding_classifier.py` | Design | Small refactor | Footgun for future changes |
| 🟠 HIGH | File too large + silent exceptions | `main.py` | Maintainability | Router split | Debugging difficult, hard to maintain |
| 🟠 HIGH | Hard-coded constants | `proxy.py, embedding_classifier.py, mcp_server.py, etc.` | Configuration | Move to config.yaml | No runtime tuning possible |
| 🟡 MEDIUM | Observability gaps | Multiple files | Logging | Add 10+ log lines | Silent failures in production |

---

## Implementation Sequence

**Week 1 (Quick Wins — Bugs + High Value)**:
1. Fix `cache.py:280` — 1 line (CRITICAL)
2. Add logging to `config.py:248` — 2 lines (CRITICAL)
3. Extract `mcp_server.py` router — 150 lines (HIGH, isolated)
4. Move MCP input limit to config — 1 change (HIGH, high value)

**Week 2 (Refactoring)**:
5. Extract `harness.py` router — 900 lines (HIGH, high value)
6. Extract other routers incrementally

**Week 3 (Observability)**:
7. Add Tap events for config reload
8. Upgrade `logger.debug` → `logger.warning` paths
9. Refactor `embedding_classifier.py` to async

**Longer term**:
10. Promote all hard-coded constants to config
11. Audit remaining ~25 silent exception handlers in main.py

---

## Files Flagged for Follow-Up

```
CRITICAL (Fix immediately):
  🔴 beigebox/cache.py              — expires_at bug
  🔴 beigebox/config.py             — silent error logging

HIGH (Fix this sprint):
  🟠 beigebox/agents/embedding_classifier.py  — async conversion
  🟠 beigebox/main.py               — split into routers
  🟠 beigebox/proxy.py              — promote hard-coded constants
  🟠 beigebox/mcp_server.py         — extract router, move config

MEDIUM (Fix next sprint):
  🟡 beigebox/cache.py              — add Tap events, upgrade logger.debug
  🟡 beigebox/main.py               — audit silent exception handlers
  🟡 Multiple files                 — promote all hard-coded constants
```

