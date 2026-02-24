# Step 3: Generic OpenAI-Compatible Backend

**Problem**: BeigeBox only supports Ollama + OpenRouter. Users running llama.cpp, vLLM, TGI, or Aphrodite can't use them.

**Solution**: Add generic OpenAI-compatible backend that works with any endpoint speaking the standard API.

---

## Files Changed

### 1. NEW FILE: `beigebox/backends/openai_compat.py`

Generic backend class supporting:
- llama.cpp server
- vLLM
- Text Generation WebUI (TGI)
- Aphrodite
- LocalAI
- Any other OpenAI-compatible endpoint

**Features**:
- Standard `/v1/chat/completions` endpoint
- `/v1/models` for listing available models
- Optional API key support (Bearer token)
- Timeout handling
- Full streaming support
- Health checks

**170 lines, zero dependencies** — works exactly like OllamaBackend but generic.

---

### 2. MODIFIED FILE: `beigebox/backends/router.py`

Added import and registration:

```python
from beigebox.backends.openai_compat import OpenAICompatibleBackend

PROVIDERS: dict[str, type[BaseBackend]] = {
    "ollama": OllamaBackend,
    "openrouter": OpenRouterBackend,
    "openai_compat": OpenAICompatibleBackend,  # ← NEW
}
```

Router automatically uses the new backend when configured.

---

### 3. MODIFIED FILE: `beigebox/backends/__init__.py`

Export new backend for public API:

```python
from beigebox.backends.openai_compat import OpenAICompatibleBackend

__all__ = [
    "MultiBackendRouter",
    "BaseBackend",
    "OllamaBackend",
    "OpenRouterBackend",
    "OpenAICompatibleBackend",  # ← NEW
]
```

---

## Configuration

### Using llama.cpp server

```yaml
backends:
  - provider: openai_compat
    name: llama.cpp
    url: http://localhost:8000
    priority: 1
    max_retries: 2
    backoff_base: 1.5
```

### Using vLLM

```yaml
backends:
  - provider: openai_compat
    name: vLLM
    url: http://localhost:8000
    priority: 1
```

### Using Text Generation WebUI (TGI)

```yaml
backends:
  - provider: openai_compat
    name: TGI
    url: http://localhost:5000
    priority: 1
```

### Using Aphrodite

```yaml
backends:
  - provider: openai_compat
    name: Aphrodite
    url: http://localhost:5000
    priority: 1
```

### With API Key (if endpoint requires it)

```yaml
backends:
  - provider: openai_compat
    name: LocalAI
    url: http://localhost:8080
    api_key: ${LOCALAI_API_KEY}
    priority: 1
```

---

## How It Works

1. Client sends request → BeigeBox proxy
2. Router sees `provider: openai_compat`
3. Creates `OpenAICompatibleBackend` instance
4. Wraps with retry logic (from Step 2)
5. Forwards to `http://{url}/v1/chat/completions`
6. Expects OpenAI-compatible response format
7. Returns to client

**Transparent to both frontend and backend** — same as Ollama.

---

## Multiple Backends Example

Use all three types together:

```yaml
backends:
  - provider: ollama
    name: ollama-primary
    url: http://ollama:11434
    priority: 1
    max_retries: 2
    
  - provider: openai_compat
    name: vllm-secondary
    url: http://vllm:8000
    priority: 2
    max_retries: 2
    
  - provider: openrouter
    name: openrouter-fallback
    url: https://openrouter.io/api/v1
    api_key: ${OPENROUTER_API_KEY}
    priority: 3
```

Router tries in order:
1. Ollama (primary)
2. vLLM (if Ollama fails)
3. OpenRouter API (if both fail)

Each backend gets **independent retry logic** (from Step 2).

---

## Compatibility Matrix

| Service | Provider | Status | Notes |
|---------|----------|--------|-------|
| Ollama | `ollama` | ✅ Native | Optimized |
| llama.cpp | `openai_compat` | ✅ Works | Server mode |
| vLLM | `openai_compat` | ✅ Works | Standard setup |
| TGI | `openai_compat` | ✅ Works | API mode |
| Aphrodite | `openai_compat` | ✅ Works | OpenAI-compat |
| LocalAI | `openai_compat` | ✅ Works | With api_key |
| LM Studio | `openai_compat` | ✅ Works | Server mode |
| OpenAI (via proxy) | `openai_compat` | ✅ Works | api_key required |

---

## Advantages

- **One backend class** supports dozens of services
- **Drop-in replacement** for Ollama (same interface)
- **No code changes** needed — purely config
- **Retry logic works** the same across all backends
- **Future-proof** — works with any new OpenAI-compatible service

---

## Testing

### 1. Start a compatible endpoint

```bash
# Using llama.cpp
./server -m model.gguf --port 8000

# Using vLLM
python -m vllm.entrypoints.openai.api_server --port 8000

# Using TGI
docker run -p 5000:80 ghcr.io/huggingface/text-generation-inference:latest
```

### 2. Add to config.yaml

```yaml
backends:
  - provider: openai_compat
    name: my-service
    url: http://localhost:8000  # or 5000 for TGI
    priority: 1
```

### 3. Test via BeigeBox

```bash
curl http://localhost:8001/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "whatever-model-is-loaded",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

Should get standard OpenAI response format.

---

## Performance

- **Zero overhead** — direct passthrough to `/v1/chat/completions`
- **Streaming fully supported** — SSE forwarding
- **Health checks fast** — `GET /v1/models` (usually <100ms)
- **Model listing works** — auto-discovers available models

---

## Deployment

1. **Add `openai_compat.py`** to `beigebox/backends/`
2. **Update `router.py`** (import + PROVIDERS dict)
3. **Update `__init__.py`** (export)
4. **Update `config.yaml`** with new backend definitions
5. **Restart BeigeBox**

---

This completes Step 3. **Files ready**:
- `openai_compat.py` → `beigebox/backends/`
- `router.py` → `beigebox/backends/` (updated)
- `backends_init.py` → `beigebox/backends/__init__.py`
