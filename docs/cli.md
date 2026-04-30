# CLI

## CLI Commands

All commands start with `beigebox` (or `python -m beigebox.cli`):

### Server Control

```bash
beigebox dial                          # Production mode (FastAPI + Uvicorn)
beigebox setup                         # Pull required models into Ollama
```

### Memory + observability

```bash
beigebox sweep <query>                 # Semantic search over stored conversations
beigebox tap                           # Tail the wiretap event stream
beigebox ring                          # Verify proxy → backends connectivity
beigebox flash                         # Stats / config snapshot
beigebox models                        # OpenRouter model catalog
beigebox rankings                      # Top-model rankings
beigebox dump                          # Dump stored conversations
```

### Benchmarking

```bash
beigebox bench [--model llama3.1:8b] [--num-runs 5] [--num-predict 120]
```

Runs speed benchmark directly against Ollama (bypasses proxy). Reports:
- Average tokens/sec
- Median tokens/sec
- Time-to-first-token (TTFT)
- Per-run breakdown

### Data & Debugging

```bash
beigebox serve-static <path>           # Serve a directory over HTTP (for file inspection)
beigebox quarantine list               # List quarantined embeddings (RAG-poisoning)
beigebox eval <suite>                  # Run an eval suite
```

See `beigebox --help` for the full command list.

> The `z:` inline command prefixes (e.g. `z: use_openrouter`, `z: temp_0.2`) and `beigebox build-centroids` were removed in v3 along with the tiered routing layer. Backends are selected per-model by `MultiBackendRouter` based on `routing.model_routes` in `config.yaml`. To force a specific model from any client, just send `model: <name>` in the request body — no inline-prefix gymnastics needed.

---

## Development

### Run locally (with auto-reload)

```bash
uvicorn beigebox.main:app --reload --port 8000
```

Then open http://localhost:8000 (web UI) or send requests to `http://localhost:8000/v1/chat/completions`.

### Run tests

```bash
pytest                                 # All tests
pytest tests/test_proxy.py             # Single file
pytest tests/test_proxy.py::test_name -v  # Single test
```

### Install in editable mode

```bash
pip install -e .
```

Changes to source files are reflected immediately (no reinstall).

---

## Environment Variables

```bash
OLLAMA_HOST=http://host.docker.internal:11434  # Ollama endpoint (macOS host-native)
GOOGLE_API_KEY=...                    # For Google search tool
GOOGLE_CSE_ID=...                     # Custom search engine ID
OPENROUTER_API_KEY=...                # OpenRouter API key
BROWSERBOX_WS_URL=...                 # BrowserBox WebSocket endpoint
```

All optional. Set in `docker/.env` for Docker Compose, or in `~/.bashrc` / systemd `EnvironmentFile=` for bare metal.

---

## See also

- [Configuration](configuration.md) — runtime settings, feature flags
- [API Reference](api-reference.md) — HTTP endpoints
- [Observability](observability.md) — querying logs
