# CLI & Z-Commands

## CLI Commands

All commands start with `beigebox`:

### Server Control

```bash
beigebox dial                          # Production mode (FastAPI + Uvicorn)
beigebox setup                         # Pull required models into Ollama
beigebox build-centroids               # Train embedding classifier (rebuild centroids)
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
```

See `beigebox --help` for full command list.

---

## Z-Commands

Z-commands let you override routing decisions inline. Prefix a message with `z: <command>`:

```
z: use_openrouter
Please write a Python function...
```

The message body (after the newline) is sent to the backend; the `z:` directive is parsed and removed.

### Reference

| Command | Effect | Example |
|---|---|---|
| `use_<model>` | Force specific model | `z: use_qwen2.5:7b` |
| `use_<backend>` | Force specific backend | `z: use_openrouter` |
| `temp_<value>` | Set temperature | `z: temp_0.2` |
| `top_p_<value>` | Set top_p | `z: top_p_0.95` |
| `max_<tokens>` | Set max_tokens | `z: max_100` |
| `ctx_<id>` | Resume session | `z: ctx_abc123def` |
| `chat` | Standard chat (no override) | `z: chat` |
| `reload` | Force model reload (keep_alive: 0) | `z: reload` |

### Examples

```
z: use_llama3.1:8b
Write me a haiku about cats.

z: temp_0.1 use_qwen2.5:7b
What is 2 + 2?

z: ctx_session-1
Summarize what we discussed earlier.
```

The Z-command is tier 1 in the routing pipeline — it bypasses all other routing logic.

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
