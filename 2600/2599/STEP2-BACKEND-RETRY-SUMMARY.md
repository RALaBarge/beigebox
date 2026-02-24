# Step 2: Backend Retry/Cooldown — Complete Implementation

**Problem**: Ollama models return 404 when still loading. BeigeBox fails immediately instead of retrying.

**Solution**: Add exponential backoff retry logic at the router level.

---

## Files Changed

### 1. NEW FILE: `beigebox/backends/retry_wrapper.py`

**Purpose**: Wrap any backend with exponential backoff retry logic.

**Key features**:
- Retries on transient errors (404, 429, 5xx)
- Exponential backoff: `base^attempt`, capped at max
- Skips non-retryable errors (401, 403, 400)
- Handles both streaming and non-streaming requests
- Transparent to router (same interface as BaseBackend)

**Configuration**:
```python
RetryableBackendWrapper(
    backend,
    max_retries=2,        # Try 2 times on transient errors
    backoff_base=1.5,     # 1.5^attempt seconds
    backoff_max=10.0,     # Cap at 10 seconds
)
```

**Backoff calculation**:
- Attempt 1: 1.5^1 = 1.5 seconds
- Attempt 2: 1.5^2 = 2.25 seconds
- Attempt 3: 1.5^3 = 3.375 seconds (capped at 10)

**Transient errors (retried)**:
- 404: Model not found / still loading
- 429: Rate limited
- 500-504: Server errors

**Permanent errors (not retried)**:
- 401: Unauthorized
- 403: Forbidden
- 400: Bad request
- Others

---

### 2. MODIFIED FILE: `beigebox/backends/router.py`

**Change**: In `MultiBackendRouter.__init__()`, wrap each backend with retry logic.

**Before**:
```python
def __init__(self, backends_config: list[dict]):
    self.backends: list[BaseBackend] = []
    for cfg in backends_config:
        backend = self._create_backend(cfg)
        if backend:
            self.backends.append(backend)  # ← No retry logic
```

**After**:
```python
def __init__(self, backends_config: list[dict]):
    self.backends: list[BaseBackend] = []
    for cfg in backends_config:
        backend = self._create_backend(cfg)
        if backend:
            # Wrap with retry logic for transient error handling
            from beigebox.backends.retry_wrapper import RetryableBackendWrapper
            max_retries = cfg.get("max_retries", 2)
            backoff_base = cfg.get("backoff_base", 1.5)
            backoff_max = cfg.get("backoff_max", 10.0)
            wrapped = RetryableBackendWrapper(
                backend,
                max_retries=max_retries,
                backoff_base=backoff_base,
                backoff_max=backoff_max,
            )
            self.backends.append(wrapped)
```

**Total changes**: 14 lines added.

---

## Configuration (in config.yaml)

Add retry parameters to your backend configurations:

```yaml
backends:
  - provider: ollama
    url: http://ollama:11434
    timeout: 120
    priority: 1
    max_retries: 2         # ← Retry transient errors
    backoff_base: 1.5      # ← 1.5^attempt seconds
    backoff_max: 10.0      # ← Cap at 10 seconds
```

**Tuning for your models**:
- Small model (3B): `max_retries: 2` (default)
- Medium model (7B-13B): `max_retries: 3, backoff_base: 2.0`
- Large model (30B+): `max_retries: 4, backoff_base: 2.0, backoff_max: 20.0`

---

## How It Works

### Scenario: Ollama Model Loading

```
Request: POST /v1/chat/completions
Model: llama2:7b (not loaded yet)

Timeline:
┌─ Attempt 1: 404 Model not found
│  └─ Transient error detected, wait 1.5s
├─ Attempt 2: 404 Model still loading
│  └─ Transient error detected, wait 2.25s
└─ Attempt 3: 200 OK (model ready) ✅
  
Total: ~3.75 seconds (was instant failure before)
```

### Scenario: Permanent Auth Error

```
Request: POST /v1/chat/completions
Backend: API with invalid key

Timeline:
├─ Attempt 1: 401 Unauthorized
│  └─ Permanent error (not retryable)
└─ FAIL immediately ✅ (no wasted retries)
```

---

## Testing

**Without retry (old behavior)**:
```bash
$ curl http://localhost:8000/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "llama2:7b", "messages": [{"role": "user", "content": "hi"}]}'

# If Ollama hasn't loaded the model yet:
# Response: {"error": "model not found", "status": 404}
```

**With retry (new behavior)**:
```bash
# Same request
# → 404, wait 1.5s, retry
# → 404, wait 2.25s, retry
# → 200 OK response ✅
```

---

## Logging

Retry attempts appear in the logs:

```
WARNING: Backend 'ollama' transient 404 for 'llama2:7b', retry in 1.5s (1/2)
WARNING: Backend 'ollama' transient 404 for 'llama2:7b', retry in 2.3s (2/2)
INFO: Backend 'ollama' served model 'llama2:7b' in 3750ms
```

---

## Deployment

1. **Add `retry_wrapper.py`** to `beigebox/backends/`
2. **Replace `router.py`** in `beigebox/backends/`
3. **Update `config.yaml`** with retry parameters (optional, defaults work)
4. **Restart BeigeBox**

That's it. Transparent to clients.

---

## Edge Cases

### Streaming Requests
Retry happens **before** streaming starts. Once streaming begins, errors are sent to client (no mid-stream retry).

### Multiple Backends
Each backend is wrapped independently. Router tries backends in priority order, and each backend retries internally.

Example with 2 backends:
```
Request → Backend 1 (try 3 times) → fail → Backend 2 (try 3 times) → success/fail
```

### Timeout Behavior
Request timeout is still `backend.timeout` (default 120s). Retry backoff is on top of that:
- 3 retry attempts with 2.5s backoff = 5 seconds overhead
- Total request can take up to 125 seconds

---

## Performance Impact

**Latency increase for failures**:
- 1 transient error: +1.5 seconds
- 2 transient errors: +3.75 seconds
- 3 transient errors: +6.125 seconds

**No impact for successes**: If backend responds immediately, wrapper returns immediately.

---

This completes Step 2. **Files ready for deployment**:
- `retry_wrapper.py` → `beigebox/backends/`
- `router.py` → `beigebox/backends/`
