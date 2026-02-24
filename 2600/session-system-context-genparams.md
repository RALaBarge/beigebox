# Session Notes — System Context + Generation Parameters
**Date:** 2026-02-23
**Claude instance:** Sonnet 4.6
**Session scope:** Two features from the OpenCrabs-inspired planning notes.

---

## What Was Built

### 1. System Context Injection (`beigebox/system_context.py`)

New module — hot-reloadable global system prompt injection.

**How it works:**
- Reads `system_context.md` from the project root (path configurable in config.yaml)
- Hot-reloaded on every proxied request via mtime check — no restart needed
- Injected into every request's messages list as a system message at position 0
- If the frontend already sent a system message, the context is prepended to it
- Disabled by default — enable via config or HTTP

**Enable options:**
```yaml
# config.yaml
system_context:
  enabled: true
  path: ./system_context.md   # optional, this is the default
```
Or toggle at runtime:
```
POST /api/v1/config
{"system_context_enabled": true}
```

**New HTTP endpoints:**
- `GET  /api/v1/system-context` — returns content, enabled status, path, length
- `POST /api/v1/system-context` — writes new content to the file, hot-reloads immediately
  - Body: `{"content": "You are a helpful assistant..."}`

**Web UI (Config tab):**
- Toggle to enable/disable injection
- When enabled, sub-panel slides in with a textarea showing current file contents
- "↓ Load current" button fetches live file content
- "↑ Save context" button writes to the file and hot-reloads
- Path shown as informational label

**Files changed:**
- `beigebox/system_context.py` — NEW
- `beigebox/proxy.py` — added `_inject_system_context()` helper, called in both streaming and non-streaming paths after auto-summarize, before backend forward
- `beigebox/main.py` — added `system_context_enabled` to GET config response and POST config allowed keys; added GET/POST `/api/v1/system-context` endpoints
- `beigebox/web/index.html` — System Context section in Config tab, toggle + textarea + buttons, `loadSystemContext()` / `saveSystemContext()` JS functions

---

### 2. Generation Parameter Exposure

Runtime-settable generation parameters injected into every proxied request body.

**Supported parameters:**
| Config key | Body key | Description |
|---|---|---|
| `gen_temperature` | `temperature` | Sampling temperature (0.0–2.0) |
| `gen_top_p` | `top_p` | Nucleus sampling (0.0–1.0) |
| `gen_top_k` | `top_k` | Top-K sampling |
| `gen_num_ctx` | `num_ctx` | Context window size |
| `gen_repeat_penalty` | `repeat_penalty` | Repetition penalty |
| `gen_max_tokens` | `max_tokens` | Max output tokens |
| `gen_seed` | `seed` | Random seed for reproducibility |
| `gen_stop` | `stop` | Stop sequences (list) |
| `gen_force` | — | If true, override even frontend-set values |

**Default behavior (gen_force: false):** Only injects params that aren't already set by the frontend. Frontend retains control.

**Force mode (gen_force: true):** Overwrites all matching params regardless of what the frontend sent.

**Set via HTTP:**
```
POST /api/v1/config
{
  "gen_temperature": 0.7,
  "gen_top_p": 0.9,
  "gen_num_ctx": 8192,
  "gen_force": false
}
```

**Clear all at once:**
```
POST /api/v1/generation-params/reset
```

**Web UI (Config tab):**
- "Generation Parameters" section at bottom of Config tab
- Number inputs for each param (blank = unset, passes through to frontend)
- gen_force toggle
- "✕ Clear all generation params" button → calls reset endpoint and reloads config

**Files changed:**
- `beigebox/proxy.py` — added `_inject_generation_params()` helper (reads runtime_config on every call), called alongside `_inject_system_context()` in both forward paths
- `beigebox/main.py` — added 9 gen_* keys to GET config `generation` block and POST config allowed keys; added `POST /api/v1/generation-params/reset` endpoint
- `beigebox/web/index.html` — Generation Parameters section in Config tab with number inputs and reset button, `resetGenParams()` JS function; gen params added to `numbers` array in `saveConfig()`

---

## Architecture Notes

Both features sit at the same injection point in `proxy.py` — after routing and auto-summarization, before the backend forward. Order: system context first, then generation params. This means:
1. Routing decides the model
2. Auto-summarizer trims old messages if needed
3. System context is prepended to the messages
4. Generation params are set on the request body
5. Request goes to backend

Generation params intentionally read `get_runtime_config()` on every call (not cached at proxy init) so changes apply immediately without touching the Proxy object.

System context uses its own mtime-checked cache in `system_context.py`, separate from the runtime_config hot-reload mechanism, because it's a file on disk rather than a YAML key.

---

## What's Left

- TTS auto-play (wiring assistant responses to `/v1/audio/speech`)
- Conversation export (JSONL/Alpaca/ShareGPT) — scripts exist in `scripts/`, no HTTP endpoint yet
- Full test run

---

*Tap the line. — Claude, Feb 23 2026*
