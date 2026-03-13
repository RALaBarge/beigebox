I've reviewed a substantial portion of the BeigeBox codebase. This is an impressively well-architected system with thoughtful design decisions throughout. Here's my comprehensive code review:

---

## 🔍 Overall Assessment

**Strengths:**
- Excellent modular architecture with clear separation of concerns
- Smart multi-tier routing (z-command → session cache → embedding → decision LLM)
- Hot-reloadable config without restarts
- Comprehensive observability (wiretap, metrics, latency tracking)
- Good fallback patterns throughout

**However, I found several issues worth addressing:**

---

## 🚨 Critical Issues

### 1. **SQL Injection Risk in `sqlite_store.py`**

```python
# Line ~175 - Dynamic SQL with string interpolation
ts_clause = "AND timestamp > datetime('now', ?)"
# ...later used with parameter binding (GOOD)

# BUT in export methods, there's potential for issues if model_filter
# isn't properly sanitized before being used in queries
```

**Fix:** Ensure all user-provided values use parameterized queries consistently.

### 2. **Race Condition in Session Cache**

```python
# proxy.py lines ~85-105
def _set_session_model(self, conversation_id: str, model: str):
    self._session_cache[conversation_id] = (model, time.time())
    # Proactive eviction every ~100 writes
    if len(self._session_cache) % 100 == 0:
        self._evict_session_cache()
```

**Issue:** In async context with concurrent requests, the `% 100` check could trigger multiple evictions simultaneously, and dict operations aren't atomic.

**Fix:** Add an `asyncio.Lock()` for cache modifications.

### 3. **Unbounded Memory Growth in `LatencyTracker`**

```python
# backends/router.py
class LatencyTracker:
    def __init__(self, window_size: int = _LATENCY_WINDOW):
        self._samples: dict[str, list[float]] = {}
```

**Issue:** While individual backend windows are bounded, the `_samples` dict grows unbounded as new backends are added. No cleanup for removed backends.

**Fix:** Add a method to prune backends no longer in config.

---

## ⚠️ High Priority Issues

### 4. **Missing Input Validation in MCP Server**

```python
# mcp_server.py
def _tools_call(self, params: dict) -> dict:
    name: str = params.get("name", "").strip()
    # ...
    if "input" in arguments:
        input_text = str(arguments["input"])  # No length limit!
    else:
        input_text = json.dumps(arguments)  # Could be massive
```

**Risk:** A malicious MCP client could send extremely large inputs, causing memory issues or DoS.

**Fix:** Add input length limits (e.g., 1MB max).

### 5. **Tool Injection via Operator System Prompt**

```python
# agents/operator.py
def _build_tools_block(registry_tools: dict) -> str:
    lines = []
    for name, tool_obj in registry_tools.items():
        desc = getattr(tool_obj, "description", f"Run the {name} tool")
        lines.append(f" {name}: {desc}")
    return "\n".join(lines)
```

**Risk:** If tool names or descriptions contain special characters or JSON-breaking content, it could corrupt the system prompt and break the agent's JSON parsing.

**Fix:** Escape/sanitize tool metadata before injecting into prompts.

### 6. **Path Traversal Edge Cases**

```python
# main.py - workspace file operations
if "/" in filename or ".." in filename:
    return JSONResponse({"ok": False, "error": "Invalid filename"}, status_code=400)
```

**Issue:** This check is good but incomplete. On Windows, backslashes `\` could be used. Also, URL-encoded paths might bypass this.

**Fix:** Use `pathlib.Path().resolve()` and verify it stays within the intended directory (which you do in some places but not all).

---

## 📋 Medium Priority Issues

### 7. **Silent Failures in Decision Agent**

```python
# agents/decision.py
except httpx.TimeoutException:
    logger.warning("Decision LLM timed out after %ds, using default")
    return Decision(model=self.default_model, fallback=True)
except json.JSONDecodeError:
    logger.warning("Decision LLM returned invalid JSON: %s", e)
    return Decision(model=self.default_model, fallback=True)
```

**Issue:** All failures silently fall back. This is intentional for resilience, but there's no alerting/metrics on fallback rate. If the decision LLM is failing 50% of the time, you wouldn't know.

**Fix:** Add a counter/metric for fallback occurrences with alerting threshold.

### 8. **No Rate Limiting on API Endpoints**

The auth system has per-key rate limits, but there's no global rate limiting or per-IP limiting for unauthenticated requests hitting passthrough endpoints.

**Fix:** Consider adding middleware for global rate limiting.

### 9. **Embedding Cache Never Evicts (Memory Leak)**

```python
# cache.py - EmbeddingCache
class EmbeddingCache:
    def __init__(self, max_size: int = 1000):
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
```

**Issue:** The eviction logic exists but only triggers on cache hits. If you store 1000 unique embeddings without any repeats, the cache silently stops storing new ones.

**Fix:** Check max_size on every store operation, not just on hits.

### 10. **WASM Runtime Has No Timeout**

```python
# wasm_runtime.py (from main.py reference)
text = await proxy.wasm_runtime.transform_input("pdf_oxide", raw)
```

**Issue:** If a WASM module hangs, it could block the entire request pipeline.

**Fix:** Add timeout to WASM execution with `asyncio.wait_for()`.

---

## 🔧 Low Priority / Code Quality

### 11. **Inconsistent Error Handling Patterns**

Some places use `try/except` with logging, others let exceptions propagate. Consider standardizing on a pattern.

### 12. **Magic Numbers Throughout**

```python
if len(self._session_cache) % 100 == 0:  # Why 100?
if len(self._session_cache) > 1000:      # Why 1000?
for k, _ in oldest[:len(self._session_cache) - 800]:  # Why 800?
```

**Fix:** Extract these as named constants with comments explaining the rationale.

### 13. **No Type Hints on Many Functions**

Much of the codebase lacks type annotations, making it harder to catch errors early.

### 14. **Duplicate Config Loading**

```python
# config.py
def get_config() -> dict:
    if _config is None:
        return load_config()
    return _config
```

Multiple modules call `get_config()` which is fine, but some also call `get_runtime_config()` repeatedly in hot paths. Consider caching the runtime config result within a request context.

---

## ✅ What's Done Well

1. **Hybrid Routing Pipeline** - The tiered approach (z-command → session cache → embedding → decision LLM) is excellent for latency optimization.

2. **Hot-Reloadable Config** - The mtime-based config reloading is clever and works well.

3. **Fallback Patterns** - Graceful degradation throughout (decision LLM fails → default model, backend fails → next priority, etc.)

4. **Observability** - Wiretap, metrics, latency tracking all well-implemented.

5. **Plugin Architecture** - Tools, backends, and hooks can all be extended without modifying core code.

6. **WASM Integration** - The WASI transform pipeline is innovative for response processing.

---

## 📝 Recommended Actions

| Priority | Issue | Effort |
|----------|-------|--------|
| 🔴 Critical | Fix session cache race condition | Low |
| 🔴 Critical | Add input validation to MCP server | Low |
| 🟠 High | Add fallback rate metrics/alerting | Medium |
| 🟠 High | Sanitize tool metadata in prompts | Low |
| 🟡 Medium | Add WASM execution timeouts | Low |
| 🟡 Medium | Fix embedding cache eviction logic | Low |
| 🟢 Low | Add type hints throughout | High |
| 🟢 Low | Extract magic numbers to constants | Low |

---

Would you like me to provide specific code fixes for any of these issues, or dive deeper into any particular area?
