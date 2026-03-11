# Backend Plugins — Custom Inference Engines

Drop custom LLM backend implementations here and BeigeBox will auto-discover and load them at startup.

## Shared Model Path

**All backends use the same model path** configured in `config.yaml`:

```yaml
backend:
  models_path: "/mnt/storage/models"  # Shared across Ollama, llama.cpp, Mini-SGLang, etc.
```

This means:
- Store your models in one place (e.g., `/mnt/storage/models/model.gguf`)
- All inference engines can access them without duplication
- No need to download models separately for each backend
- Use `${MODELS_PATH}` env var override in Docker

**Example Docker setup:**
```yaml
services:
  ollama:
    volumes:
      - /mnt/storage/models:/root/.ollama/models

  llama-cpp:
    volumes:
      - /mnt/storage/models:/models
    command: --models-path /models

  mini-sglang:
    volumes:
      - /mnt/storage/models:/models
    command: --model-dir /models
```

All three see the same model files — no redundant storage.

## Quick Start

1. **Create a new Python file** with your backend implementation
2. **Inherit from `BaseBackend`** and implement abstract methods
3. **Drop it in this directory** — it auto-loads on startup
4. **Use in `config.yaml`** — reference by provider name (converted from class name)

## Example: llama.cpp

```python
# backends/plugins/my_custom_engine.py

from beigebox.backends.base import BaseBackend, BackendResponse
import httpx

class MyCustomEngineBackend(BaseBackend):
    async def forward(self, body: dict) -> BackendResponse:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/v1/chat/completions",
                json=body,
                timeout=self.timeout,
            )
            return BackendResponse(
                ok=True,
                data=resp.json(),
                backend_name=self.name,
            )

    async def forward_stream(self, body: dict):
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", f"{self.url}/v1/chat/completions", json=body) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield line

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.url}/health")
                return resp.status_code == 200
        except:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.url}/v1/models")
                return [m["id"] for m in resp.json().get("data", [])]
        except:
            return []
```

## Config Usage

Once you define your backend, reference it by provider name (class name → snake_case):

```yaml
# config.yaml
backends_enabled: true

backends:
  # Built-in backends
  - provider: ollama
    name: local-ollama
    url: http://localhost:11434
    priority: 1

  # Your custom backend (auto-discovered)
  - provider: my_custom_engine        # ← Class name MyCustomEngineBackend → my_custom_engine
    name: my-engine
    url: http://localhost:8000
    priority: 2

  - provider: llama_cpp               # ← Included example
    name: llama-cpp
    url: http://localhost:9000
    priority: 3
```

## Included Examples

- **llama.cpp** (`llama_cpp.py`) — Ultra-lightweight C++ inference, ~15KB binary
- **Mini-SGLang** (`mini_sglang.py`) — Clean, readable 5k-line serving framework
- **ExecuTorch** (`executorch.py`) — Meta's embedded engine, 50KB footprint

## Abstract Methods (Required)

Every backend must implement:

### `async def forward(self, body: dict) -> BackendResponse`
Non-streaming chat completion. Return `BackendResponse` with:
- `ok: bool` — success/failure
- `data: dict` — OpenAI-compatible response data
- `error: str` — error message (if ok=False)

### `async def forward_stream(self, body: dict)`
Streaming chat completion. Yield SSE lines (strings starting with `data: `).

### `async def health_check(self) -> bool`
Return True if backend is healthy and reachable. Called periodically by router.

### `async def list_models(self) -> list[str]`
Return list of model names available on this backend.
Store in `self._available_models` for router's `supports_model()` check.

## How It Works

1. **Startup** — BeigeBox calls `load_backend_plugins("backends/plugins")`
2. **Discovery** — Loader finds all `.py` files and imports them
3. **Registration** — Each `BaseBackend` subclass is extracted and registered
4. **Name conversion** — `MyEngineBackend` → `my_engine` (class name → snake_case)
5. **Router integration** — `PROVIDERS` dict updated with your backend
6. **Config usage** — Use provider name in `config.yaml` backends list

## Latency & Failover

The router tracks per-backend latency (P95) and can:
- **Deprioritize** slow backends via `latency_p95_threshold_ms`
- **Failover** to next priority backend on error
- **A/B split** traffic across backends via `traffic_split` weights

Your backend just needs to respond with correct `BackendResponse`; the router handles the rest.

## Tips

- **Test first** — ensure your HTTP server returns OpenAI-compatible format
- **Error handling** — always catch exceptions and return `BackendResponse(ok=False, error=...)`
- **Timeout** — respect `self.timeout` from config
- **Logging** — use `logger.error/warning()` for debugging

## Common Engines

See [TESTING.md](../../TESTING.md) for a curated list of lightweight inference engines:
- llama.cpp (15KB, C++)
- Mini-SGLang (5k lines, Python)
- InferLLM (ARM/RISC-V optimized)
- ExecuTorch (50KB, Meta)
- SimpleLLM (~950 lines, educational)
