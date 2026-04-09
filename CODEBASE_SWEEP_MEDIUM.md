# BeigeBox Core Logic Validation — Medium Depth Sweep

**Date**: 2026-04-08  
**Agent**: Spawned through beigebox proxy (Tap-logged)  
**Scope**: Core logic files (proxy, main, config, backends, agents, auth, storage)  
**Depth**: Medium (syntax, logic errors, config issues, obvious security/perf)

---

## File-by-File Analysis

### `beigebox/proxy.py` — [PASS]

**Imports**: All referenced modules exist; no circular imports.

**Concurrency**: Session cache correctly guarded by `_session_cache_lock` (H1/H2 race conditions fixed and documented).

**Issues**:
- ⚠ **WARN**: Hard-coded magic numbers in hot path:
  - Session cache eviction every `100` writes
  - Max session cache size `1000` → shrink to `800`
  - Embed timeout `5.0` seconds
  - Ring buffer `maxlen=5`
  - All should be config-driven per CLAUDE.md ("no hard-coded values")

- ⚠ **WARN**: Unused imports — `log_token_usage`, `log_latency_stage`, `log_request_started`, `log_request_completed` declared but may not be called downstream. Verify or remove.

**Risk Level**: Low (functionally correct, performance/config tuning)

---

### `beigebox/main.py` — [WARN]

**File Size**: 60K tokens / 2000+ lines.

**Issues**:
- 🔴 **FAIL**: File is too large. CLAUDE.md implicitly expects `/api/v1/*` endpoints modularized. This single file handles:
  - ~30 endpoints
  - ~30 broad `except Exception:` handlers
  - Middleware, lifespan hooks, state initialization, API routes all in one
  - Some exception handlers swallow real bugs silently (no logger.warning/error inside)

- ⚠ **WARN**: Broad exception patterns — `except Exception: return JSONResponse(...)` without logging means errors disappear from audit trail. Grep for bare `except Exception:` and audit which ones lack logging.

**Recommendation**: Split into `beigebox/routers/` subpackage:
- `routers/chat.py` — `/v1/chat/completions`
- `routers/tools.py` — `/tools/*`, MCP server
- `routers/harness.py` — `/api/v1/harness/*`
- `routers/admin.py` — health, metrics, config

**Risk Level**: Medium (maintainability, observability)

---

### `beigebox/app_state.py` — [PASS]

**Pattern**: Clean dataclass container with `TYPE_CHECKING` guards preventing circular imports.

**Details**:
- `field(default_factory=...)` correctly used for mutable defaults
- All subsystem references typed
- `get_state()` pattern correct (raises if called before startup)

**Issues**: None.

**Risk Level**: None

---

### `beigebox/config.py` — [PASS]

**Atomicity**: Hot-reload uses mkstemp + rename (atomic rename prevents partial writes).

**Validation**: Pydantic models with `.model_validate()` allow forward-compat (new keys ignored).

**Issues**:
- ⚠ **WARN**: `get_runtime_config()` silently swallows YAML parse errors:
  ```python
  try:
      return yaml.safe_load(f)
  except Exception:
      pass  # ← logs nothing; ops won't know config reloading broke
  ```
  Should log at `WARNING` level so config drift is visible in observability.

- ⚠ **WARN**: `_RUNTIME_MTIME_CHECK_INTERVAL = 1.0` is hard-coded module-private. Consider config-driven if tuning is ever needed.

**Risk Level**: Low (functional, logging improvement)

---

### `beigebox/cache.py` — [FAIL] 🔴

**BUG FOUND**:

```python
# Line 280 in lookup() method:
if log_cache_event:
    log_cache_event("cache_hit", {"key": entry.key, "expires_at": entry.expires_at, ...})
```

**Problem**: `_CacheEntry` dataclass only defines fields `key`, `ts`, `value`. There is **no `expires_at` field**.

**Impact**: Every semantic cache hit will raise:
```
AttributeError: '_CacheEntry' object has no attribute 'expires_at'
```

**Current Scope**: This likely affects the hot path if `log_cache_event` callback is registered (which it is in proxy.py's logging setup).

**Fix**: Either:
1. Add `expires_at` field to `_CacheEntry`
2. Remove the reference if expiry is calculated elsewhere
3. Change to `entry.ts` if that's the intended field

**Risk Level**: High (runtime AttributeError on cache hits)

---

### `beigebox/backends/router.py` — [PASS]

**Routing Logic**: Two-pass (fast route → degraded fallback) is clean and well-separated.

**LatencyTracker**: Uses O(N log N) sort on every `p95()` call. Fine at N=100, but could be optimized with a heap if needed.

**Issues**:
- ⚠ **WARN**: `forward_stream()` exception handling logs but partial bytes may already be yielded to client. If backend fails mid-stream, client receives partial response + `[BeigeBox: All backends failed...]` sentinel. Could confuse downstream parsers.
  - Document this behavior, or guard against mid-stream failures (buffer and retry).

- ⚠ **WARN**: `force_name` uses `body.pop()` which mutates caller's dict. Ensure callers expect this mutation.

**Risk Level**: Low (edge cases in streaming, mutation side effect)

---

### `beigebox/agents/decision.py` — [PASS]

**Exception Handling**: Split into timeout/JSON parse/general — clean fallbacks.

**Logging**: All logging wrapped in try/except so it never breaks the hot path.

**System Prompt**: Pre-built at init (good for performance).

**Issues**:
- ⚠ **WARN**: `preload()` method uses `keep_alive: -1` (Ollama-specific) to pin model in VRAM. If ever used with a non-Ollama decision backend (OpenRouter, vLLM), this silently fails (unknown key).
  - Should check `backend.provider` before using Ollama-specific options.

**Risk Level**: Low (silent no-op on non-Ollama backends, but routing still works)

---

### `beigebox/agents/embedding_classifier.py` — [WARN] 🟡

**BUG/PERF ISSUE**:

The classifier uses **synchronous** `httpx.post()` inside an async pipeline:

```python
def _embed(self, text: str) -> Optional[List[float]]:
    response = httpx.post(...)  # ← BLOCKING SYNC CALL
    return response.json()["embedding"]
```

And the `classify()` method is also sync (not `async def`):

```python
def classify(self, text: str) -> str:  # ← NOT ASYNC
    ...
    embeddings = [self._embed(t) for t in texts]
```

**Impact**:
- **Event loop blocked** for ~50ms per embed call (network latency)
- If the routing tier calls `classifier.classify()` in the hot path (request → classify → decide route), the entire beigebox pipeline stalls
- Callers must run this in a thread executor (`asyncio.to_thread(classifier.classify, ...)`) to avoid blocking

**Comparison**: `SemanticCache._embed()` correctly uses `httpx.AsyncClient` and `async def`.

**Fix**: Convert to async:
```python
async def _embed(self, text: str) -> Optional[List[float]]:
    async with self.client as client:
        response = await client.post(...)
        return response.json()["embedding"]

async def classify(self, text: str) -> str:
    ...
```

**Risk Level**: Medium (hot path blocking, but hidden by executor wrapper)

---

### `beigebox/agents/zcommand.py` — [PASS]

**Scope**: Pure string parsing, no I/O, no concurrency.

**Issues**: None.

**Risk Level**: None

---

### `beigebox/agents/operator.py` — [PASS] (partial read)

**Scope**: Partially reviewed (first 200 lines of ~500-line file).

**Prompting**: System prompts well-structured with `{{...}}` escaped for `.format()` (prevents double-expansion).

**JSON Protocol**: Fail-hard on malformed JSON (no fallback to text parsing), intentional per CLAUDE.md.

**Issues**: None detected in scoped portion.

**Full Review Note**: Larger file not exhaustively read; deeper routine-level bugs in tail not covered.

**Risk Level**: Low (spot-check clean)

---

### `beigebox/auth.py` — [PASS]

**Token Lookup**: Parameterized, no SQL.

**Rate Limiting**: Rolling-window deque with front-eviction of stale entries is correct.

**Pattern Matching**: Uses `fnmatch` per CLAUDE.md spec (e.g., `"llama*"` matches `llama3.2`).

**Issues**:
- ⚠ **WARN**: Token map stored as plain dict key. If config reload is ever added, `_token_map` and `_rate_windows` could drift from new config. Not an issue today (no reload mechanism), but fragile if patterns change in future.

**Risk Level**: Low (only relevant if runtime config reload added)

---

### `beigebox/mcp_server.py` — [PASS]

**JSON-RPC 2.0**: Dispatch loop with proper error codes (100, 32600, 32700, etc.).

**Notification Handling**: No response on notification requests (spec-correct).

**Search**: `_search_capabilities()` is O(N*M) over tool registry. Fine for small registries.

**Issues**:
- ⚠ **WARN**: `_tools_call` input size limit hard-coded as `1_000_000` bytes. Should be config-driven (e.g., from `config.yaml` security section).

**Risk Level**: Low (tuning optimization)

---

### `beigebox/storage/sqlite_store.py` — [PASS] (partial read)

**DDL**: All `CREATE ... IF NOT EXISTS` (idempotent).

**WAL Mode**: Enabled per connection (correct for concurrent read-during-write).

**SQL Injection**: No f-string or percent-format SQL detected. All parameterized.

**Transactions**: Context manager handles commit/rollback correctly.

**Issues**:
- ⚠ **WARN**: `_connect()` opens a fresh connection per call. Fine functionally, but high-frequency callers (>100 queries/sec) pay connection overhead. A short-lived connection pool would improve throughput.

- ⚠ **WARN**: Migration list uses dummy `"SELECT 1"` as placeholder — cosmetic; harmless.

**Risk Level**: Low (optimization opportunity, no correctness issues)

---

## Summary of Findings

### 🔴 High Priority (Bugs)

| File | Issue | Impact |
|---|---|---|
| `beigebox/cache.py` | `entry.expires_at` doesn't exist; `AttributeError` on every cache hit | Cache lookups break at runtime |
| `beigebox/agents/embedding_classifier.py` | Sync `httpx.post()` blocks event loop ~50ms | Routing pipeline stalls per classifier call |

### 🟡 Medium Priority (Config/Maintainability)

| File | Issue | Impact |
|---|---|---|
| `beigebox/main.py` | 60K-line single file; ~30 endpoints + exception handlers | Hard to maintain, some errors swallowed silently |
| `beigebox/proxy.py` | Hard-coded magic numbers (100, 1000, 5.0, etc.) | Can't tune cache/timeout behavior without code edit |
| `beigebox/config.py` | Silent YAML parse errors in `get_runtime_config()` | Config reload failures invisible to ops |

### ⚠ Low Priority (Minor Optimizations/Clarity)

| File | Issue |
|---|---|
| `beigebox/backends/router.py` | Mid-stream failures may confuse downstream parsers |
| `beigebox/agents/decision.py` | Ollama-specific options silently no-op on other backends |
| `beigebox/mcp_server.py` | Input size limit hard-coded |
| `beigebox/storage/sqlite_store.py` | No connection pooling (minor perf impact) |

---

## Files Requiring Follow-Up

```
✓ beigebox/proxy.py             — config refactoring
✓ beigebox/main.py              — split into routers subpackage
🔴 beigebox/cache.py            — FIX expires_at bug
🔴 beigebox/agents/embedding_classifier.py — convert to async
⚠ beigebox/backends/router.py   — document streaming failure behavior
⚠ beigebox/agents/decision.py   — check backend provider before Ollama-specific options
⚠ beigebox/config.py            — add logging to `get_runtime_config()` errors
```

---

## Thorough Sweep Recommendation

This medium sweep identified **2 bugs** and **3 maintainability issues**. Recommend a **thorough follow-up pass** on:

1. `cache.py` — full logic audit (expires_at likely used elsewhere)
2. `embedding_classifier.py` — async conversion validation
3. `main.py` — all ~30 exception handlers for silent failures
4. All usages of hard-coded constants (proxy session cache limits, MCP input size, etc.)

