# Adding New Backend Support to BeigeBox

This guide explains the architecture and steps required to add support for a new inference backend.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Client Request (OpenAI-compatible /v1/chat/completions format)          │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
                   ┌───────────────────────┐
                   │  BeigeBox Proxy       │
                   │  (proxy.py)           │
                   └───────────┬───────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
        ┌──────────────────┐  ┌──────────────────┐
        │  Routing Layer   │  │  Decision LLM    │
        │  (router.py)     │  │  (classifier)    │
        └────────┬─────────┘  └──────────────────┘
                 │
         ┌───────┴────────────────────────┐
         │                                │
         ▼                                ▼
    ┌─────────────────────────────────────────────┐
    │  MultiBackendRouter (priority-based failover) │
    │  - Latency tracking (P95)                    │
    │  - Traffic splitting (weighted random)      │
    │  - Fallback chains                          │
    └──────────┬──────────────────────────────────┘
               │
        ┌──────┴─────────────┬──────────────┬──────────┐
        │                    │              │          │
        ▼                    ▼              ▼          ▼
    ┌────────┐   ┌──────────────┐  ┌────────────┐  ┌────────┐
    │ Ollama │   │ OpenRouter   │  │OpenAI-Cmp  │  │ Custom │
    │Backend │   │  Backend     │  │  Backend   │  │Backends│
    └────────┘   └──────────────┘  └────────────┘  └────────┘
        │              │                  │              │
        ▼              ▼                  ▼              ▼
    HTTP POST /v1/chat/completions → Unified OpenAI-compatible request
```

## Backend Integration Methods

### 1. **Easy: Use OpenAI-Compatible Endpoint (Recommended)**

**Best for:** Inference servers that already expose `/v1/chat/completions`
- llama.cpp server
- vLLM
- Text Generation WebUI (TGI)
- LocalAI
- Ollama itself

**What you need to do:**
1. Start your OpenAI-compatible server on a known URL
2. Add to `config.yaml`:
   ```yaml
   backends:
     - provider: openai_compat
       name: my-server
       url: http://localhost:8000
       timeout: 120
       timeout_ms: 60000
       priority: 1
       api_key: ""  # if needed
   ```

**Time investment:** 5 minutes (just config)

**Example: Adding Unsloth (with wrapper)**

Unsloth doesn't expose OpenAI endpoints natively, but you can:
```bash
# Start unsloth with a wrapper (e.g., using vLLM or creating one)
# Then point BeigeBox at the wrapper's /v1 endpoint
docker run -p 8000:8000 \
  -e MODEL=meta-llama/Llama-2-7b \
  unsloth-with-openai-wrapper:latest
```

Then add to config:
```yaml
backends:
  - provider: openai_compat
    name: unsloth-wrapped
    url: http://localhost:8000
    priority: 2
```

---

### 2. **Medium: Built-in Backend Class**

**Best for:** Services with custom protocol or special handling (Ollama, OpenRouter)

**What you need to do:**
1. Create `beigebox/backends/your_backend.py`
2. Inherit from `BaseBackend` and implement:
   - `async def forward(self, body: dict) -> BackendResponse` (required)
   - `async def stream(self, body: dict) -> AsyncIterator[BackendResponse]` (optional)
   - `async def list_models(self) -> list[str]` (optional)
   - `async def health_check(self) -> bool` (optional)

3. Register in `beigebox/backends/router.py`:
   ```python
   PROVIDERS: dict[str, type[BaseBackend]] = {
       "ollama": OllamaBackend,
       "openrouter": OpenRouterBackend,
       "openai_compat": OpenAICompatibleBackend,
       "your_backend": YourBackend,  # ← Add here
   }
   ```

4. Use in `config.yaml`:
   ```yaml
   backends:
     - provider: your_backend
       name: instance-name
       url: http://...
       timeout: 120
       priority: 2
   ```

**Time investment:** 30-60 minutes (implementation + testing)

**Minimal example:**
```python
# beigebox/backends/unsloth.py
from beigebox.backends.base import BaseBackend, BackendResponse
import httpx
import time

class UnslothBackend(BaseBackend):
    """Direct integration with Unsloth Python API."""

    async def forward(self, body: dict) -> BackendResponse:
        t0 = time.monotonic()
        try:
            # Import unsloth library
            from unsloth import FastLanguageModel

            # Construct prompt from messages
            messages = body.get("messages", [])
            prompt = self._format_messages(messages)

            # Run inference (blocking, so wrap in executor)
            output = await self._run_unsloth_inference(prompt, body)

            latency = (time.monotonic() - t0) * 1000

            return BackendResponse(
                ok=True,
                data=self._format_response(output),
                backend_name=self.name,
                latency_ms=latency,
                cost_usd=None,
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            return BackendResponse(
                ok=False,
                error=str(e),
                backend_name=self.name,
                latency_ms=latency,
            )

    # Helper methods...
```

---

### 3. **Advanced: Plugin Backend (Dynamic Loading)**

**Best for:** Third-party backends you want to add without modifying core code

**What you need to do:**
1. Create `beigebox/backends/plugins/your_backend.py`
2. Define a `*Backend` class inheriting from `BaseBackend`
3. Plugin auto-discovers when BeigeBox starts

**Registration:** Automatic via `plugin_loader.py` (class name → provider name)
- `UnslothBackend` → `unsloth`
- `MyServiceBackend` → `my_service`

**Config:**
```yaml
backends:
  - provider: unsloth  # auto-discovered from plugins/
    name: unsloth-1
    url: ...
    priority: 2
```

**Time investment:** 30-60 minutes (same as #2, but in plugins/ dir)

---

## BaseBackend Interface

```python
class BaseBackend(abc.ABC):
    def __init__(self, name: str, url: str, timeout: int = 120,
                 priority: int = 1, timeout_ms: int | None = None):
        """Initialize backend with connection params."""
        pass

    @abc.abstractmethod
    async def forward(self, body: dict) -> BackendResponse:
        """
        Forward a single non-streaming request.

        Args:
            body: OpenAI-compatible chat completion request

        Returns:
            BackendResponse with ok=True/False, data, latency_ms, cost_usd
        """
        pass

    async def stream(self, body: dict) -> AsyncIterator[BackendResponse]:
        """
        Stream response tokens one-by-one.
        Optional: defaults to raising NotImplementedError.

        Yields:
            BackendResponse objects with partial data (one choice/delta per response)
        """
        pass

    async def list_models(self) -> list[str]:
        """
        Return available model names.
        Optional: defaults to empty list.
        """
        pass

    async def health_check(self) -> bool:
        """
        Ping the backend to verify it's alive.
        Optional: defaults to True.
        """
        pass
```

## BackendResponse Format

```python
@dataclass
class BackendResponse:
    ok: bool                      # True if successful
    status_code: int = 200        # HTTP status
    data: dict = {}               # OpenAI response or partial chunk
    backend_name: str = ""        # Which backend handled it
    latency_ms: float = 0.0       # How long it took
    cost_usd: float | None = None # Only for API backends (e.g., OpenRouter)
    error: str = ""               # Error message if !ok
```

## Configuration Reference

```yaml
backends:
  - provider: openai_compat|ollama|openrouter|<your_backend>
    name: unique-instance-name      # For logging + routing decisions
    url: http://localhost:8000      # Service endpoint
    timeout: 120                    # Global timeout (seconds) - fallback
    timeout_ms: 60000              # Per-backend override (ms) - takes precedence
    priority: 1                     # Lower = tried first (1, 2, 3...)
    max_retries: 2                  # Retry on failure
    backoff_base: 1.5              # Exponential backoff multiplier
    backoff_max: 10.0              # Max backoff (seconds)
    api_key: ${ENV_VAR}            # Optional, for auth
    latency_p95_threshold_ms: 0    # Optional: deprioritize if P95 exceeds (0=disabled)
    allowed_models:                # Optional: whitelist models this backend can serve
      - llama2*
      - mistral*
```

## Latency-Aware Routing

Once a backend is integrated, the router automatically:
1. Tracks P95 latency of recent requests (rolling 100-sample window)
2. If P95 exceeds `latency_p95_threshold_ms`, deprioritizes on first pass
3. Uses as fallback if all healthy backends fail

Example: If unsloth is slow on large models:
```yaml
backends:
  - provider: unsloth
    name: unsloth-7b
    url: ...
    priority: 1
    latency_p95_threshold_ms: 5000  # Deprioritize if P95 > 5 seconds
```

## Traffic Splitting (A/B Testing)

If multiple backends are present, you can split traffic for A/B testing:
```yaml
backends:
  - provider: unsloth
    name: unsloth-exp
    url: http://localhost:8000
    priority: 1
    traffic_split: 0.5  # 50% traffic
  - provider: ollama
    name: ollama-control
    url: http://localhost:11434
    priority: 1
    traffic_split: 0.5  # 50% traffic
```

Router uses weighted random selection based on `traffic_split` weights.

## Testing Your Backend

```python
# test_unsloth_backend.py
import asyncio
from beigebox.backends.unsloth import UnslothBackend

async def test():
    backend = UnslothBackend(
        name="test",
        url="http://localhost:8000",
        timeout=30
    )

    # Test non-streaming
    response = await backend.forward({
        "model": "unsloth-7b",
        "messages": [{"role": "user", "content": "Hello"}],
    })

    assert response.ok
    assert response.latency_ms > 0
    print(f"✓ Latency: {response.latency_ms}ms")
    print(f"✓ Content: {response.content[:100]}")

    # Test streaming
    async for chunk in backend.stream({...}):
        print(f"  Token: {chunk.data.get('choices', [{}])[0].get('delta', {}).get('content')}")

asyncio.run(test())
```

## Unsloth-Specific Recommendations

Since Unsloth doesn't expose OpenAI endpoints natively, you have three options:

### Option A: Wrapper Server (Easiest)
```bash
# Create a thin Flask/FastAPI wrapper around Unsloth
# Expose /v1/chat/completions
# Use openai_compat backend
```

**Pros:** No core code changes, flexible, testable separately
**Cons:** Extra service overhead

### Option B: Custom UnslothBackend Class
```python
# Directly import and use unsloth Python library
# Async wrapper around synchronous Unsloth API
# Handle tokenization, inference, response formatting
```

**Pros:** Direct integration, full control
**Cons:** Unsloth's blocking I/O requires executor thread pool

### Option C: Docker Compose Service
```yaml
services:
  unsloth:
    image: unsloth:latest
    ports: ["8000:8000"]
    command: "unsloth-server --model meta-llama/Llama-2-7b"
```

Add as `openai_compat` backend.

**Pros:** Isolated service, easy to restart
**Cons:** Need to create/maintain Dockerfile

---

## Quick Start: Adding Your Backend

1. **Choose method** (Easy #1 → Medium #2 → Advanced #3)
2. **Implement** (code + tests)
3. **Configure** in `config.yaml`
4. **Test** with `/api/v1/models` and chat endpoint
5. **Monitor** latency/errors in Dashboard

**All integrations are transparent to clients** — they see a unified OpenAI-compatible endpoint regardless of which backend handled the request.
