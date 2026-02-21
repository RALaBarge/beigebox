# Multi-Backend Design (v0.6.0)

## Overview

**Multi-Backend Router** allows BeigeBox to use multiple LLM backends (Ollama, OpenRouter, etc.) with intelligent fallback and load distribution.

## Problem Statement

Current BeigeBox:
- Uses single backend (usually local Ollama)
- If backend goes down, chat stops working
- Can't leverage API backends (OpenRouter, Together, etc.)
- Can't balance load across instances

Multi-backend solves all three problems.

## Design Decisions

### 1. Architecture

```
Request → MultiBackendRouter
    ├─ Check priority order
    ├─ Try backend 1 (Ollama, priority 1)
    │   ├─ Available? → Use it
    │   └─ Timeout? → Try next
    ├─ Try backend 2 (OpenRouter, priority 2)
    │   ├─ Available? → Use it
    │   └─ Failed? → Try next
    └─ Try backend 3 (fallback)
        └─ All failed? → Graceful error
```

### 2. Backend Abstraction

```python
class BaseBackend:
    async def forward(model: str, body: dict) -> dict
    async def health_check() -> bool
    available_models: list[str]
    timeout: int
    priority: int
```

Implementations:
- `OllamaBackend` — local via `/v1/` endpoints
- `OpenRouterBackend` — API via OpenRouter

### 3. Configuration

```yaml
backends:
  - name: "local"
    url: "http://localhost:11434"
    provider: "ollama"
    priority: 1
    timeout: 120
    
  - name: "openrouter"
    url: "https://openrouter.ai/api/v1"
    provider: "openrouter"
    api_key: "${OPENROUTER_API_KEY}"
    priority: 2
    timeout: 60

backends_enabled: false
```

### 4. Model Routing

When user requests a model:
1. Which backends support this model?
2. Sort by priority
3. Try each in order
4. Use first that responds within timeout

### 5. Fallback Strategy

```
Try primary (local) with 120s timeout
  ├─ Success? → Use it
  ├─ Timeout? → Fallback
  └─ Failed? → Fallback

Fallback to secondary (OpenRouter) with 60s timeout
  ├─ Success? → Use it
  ├─ Timeout? → Fallback
  └─ Failed? → Fallback

All failed? → Return error (graceful)
```

## Implementation Notes

### Code Structure

```python
class MultiBackendRouter:
    def __init__(self, backends_config: list):
        self.backends = {}
        self.priority = {}
        # Initialize each backend
    
    async def forward_request(self, model: str, body: dict) -> dict:
        # Find capable backends
        # Try in priority order
        # Return first success or error
```

### Error Handling

```python
try:
    response = await backend.forward(model, body)
    if response.ok:
        return response
except asyncio.TimeoutError:
    logger.warning(f"Backend {name} timed out")
    continue
except Exception as e:
    logger.warning(f"Backend {name} failed: {e}")
    continue

raise RuntimeError("All backends exhausted")
```

### Logging

Each attempt logged to wiretap:
- Backend name
- Model
- Status (success/timeout/error)
- Latency
- Final backend used

## API Endpoints

No new endpoints. Transparent to clients:
- `POST /v1/chat/completions` routes automatically
- `GET /v1/models` lists all available models from all backends

## Configuration

See config.yaml for template.

Key options:
- `backends_enabled` — enable/disable multi-backend
- `backends[*].priority` — lower = try first
- `backends[*].timeout` — per-backend timeout

## Example Flow

```
User: Model="gpt-4-turbo" (only in OpenRouter)
Router: 
  1. Find backends supporting "gpt-4-turbo"
  2. Only OpenRouter has it
  3. Try OpenRouter
  4. Success → Use it

User: Model="llama3.2" (in both)
Router:
  1. Find backends supporting "llama3.2"
  2. Both Ollama and OpenRouter have it
  3. Try Ollama first (priority 1)
  4. Success → Use it (cheaper, faster)

User: Model="llama3.2" (but Ollama is down)
Router:
  1. Try Ollama (priority 1)
  2. Timeout after 120s
  3. Try OpenRouter (priority 2)
  4. Success → Use it (fallback works)
```

## Testing

- [ ] Single backend works (backward compatible)
- [ ] Primary backend succeeds (uses it)
- [ ] Primary backend times out (fallback works)
- [ ] All backends fail (graceful error)
- [ ] Model in multiple backends (priority respected)
- [ ] Model in only one backend (tries only that one)
- [ ] Cost tracking with OpenRouter

## Future Enhancements

- Load balancing (round-robin instead of strict priority)
- Health check retries (periodically re-test failed backends)
- Cost-aware routing (prefer cheaper for same quality)
- Rate limiting per backend
- Regional backend selection
