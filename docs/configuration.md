# Configuration

BeigeBox uses two configuration files:

- **`config.yaml`** — Static, startup-only (backends, models, storage)
- **`runtime_config.yaml`** — Hot-reload, no restart (defaults, toggles)

Plus **`_window_config`** in request body (per-pane overrides, highest priority).

## config.yaml

```yaml
backends:
  ollama:
    url: http://host.docker.internal:11434  # macOS host-native Ollama (Metal); Linux can use http://ollama:11434 with in-container service
  openrouter:
    url: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}

models:
  llama3.1:8b:
    backend: ollama
    gpu_layers: 30        # Enable GPU for this model
    context_length: 4096
  qwen2.5:7b:
    backend: ollama

feature_flags:
  semantic_cache:
    enabled: false
  decision_llm:
    enabled: false
  auto_summarization:
    enabled: false

auth:
  api_key: ${BEIGEBOX_API_KEY}
  keys: []              # Multi-key setup — see Authentication doc
```

## runtime_config.yaml

Hot-reloaded on every request (mtime-checked):

```yaml
default_model: llama3.1:8b
default_temperature: 0.7
default_top_p: 0.9

feature_toggles:
  semantic_cache: false
  auto_summarization: false
  operator: false
```

## Per-model options

In `config.yaml` `models:` section:

```yaml
models:
  llama3.1:8b:
    backend: ollama
    gpu_layers: 30            # GPU layers (0 = CPU only)
    context_length: 4096
    repeat_penalty: 1.1
    temperature: 0.7
    top_p: 0.9
```

## Window config (request-level)

Highest priority. Sent in request body as `_window_config`:

```json
{
  "model": "llama3.1:8b",
  "messages": [...],
  "_window_config": {
    "session_id": "sess-1",
    "temperature": 0.2,
    "top_p": 0.95,
    "num_predict": 100,
    "keep_alive": "5m",
    "force_reload": false
  }
}
```

## Feature flags

All disabled by default. Enable in `config.yaml`:

```yaml
feature_flags:
  semantic_cache:
    enabled: true
  decision_llm:
    enabled: true
  auto_summarization:
    enabled: true
  operator:
    enabled: true
  cost_tracking:
    enabled: true
```

## Environment variables

Used by config via `${VAR_NAME}` syntax:

```bash
BEIGEBOX_API_KEY=sk-...
OPENROUTER_API_KEY=sk-...
GOOGLE_API_KEY=...
GOOGLE_CSE_ID=...
OLLAMA_HOST=http://host.docker.internal:11434
```

## System context injection

Create `system_context.md` in the data directory. It's prepended to every system message. Hot-reloaded.

```markdown
# System Instructions

You are a helpful assistant.
- Be concise
- Use examples
- Cite sources
```

---

See [Architecture](architecture.md) for how config is loaded and applied.
