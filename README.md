# BeigeBox

**Tap the line. Control the carrier.**

Transparent middleware proxy for local LLM stacks. Sits between your frontend (Open WebUI, etc.) and your backend (Ollama, etc.) and intercepts every request to add intelligent routing, storage, observability, tooling, and security — without either end knowing it's there.

```
+---------------+         +--------------------------------------+         +---------------+
|               |  HTTP   |            BEIGEBOX                  |  HTTP   |               |
|  Open WebUI   | ------->|  FastAPI Proxy · Port 8000           | ------> |  Ollama /     |
|  (Frontend)   |<------- |                                      |<------- |  llama.cpp    |
|               |         |  Hybrid Router                       |         |  (Backend)    |
|  Port 3000    |         |  0. Session Cache  (instant)         |         +---------------+
+---------------+         |  1. Z-Commands     (instant)         |
                          |  2. Agentic Scorer (instant)         |         +---------------+
                          |  3. Embedding Class (~50ms)          |  HTTP   |  OpenRouter   |
                          |  4. Decision LLM   (~500ms)          | ------> |  (Fallback)   |
                          |                                      |         +---------------+
                          |  SQLite · ChromaDB · Tools           |
                          |  Operator Agent · Observability      |
                          +--------------------------------------+
```

---

## What It Does

Your frontend thinks it's talking to Ollama. Ollama thinks requests are coming straight from your frontend. BeigeBox is in the middle, transparently:

- **Storing** every conversation in SQLite + ChromaDB (you own the data)
- **Routing** requests to the right model based on query complexity
- **Falling back** across backends with priority-based failover
- **Tracking costs** for API backends including streaming
- **Augmenting** requests with tool output before they reach the LLM
- **Summarizing** long conversations automatically to manage context window
- **Forwarding** all known OpenAI and Ollama endpoints, with a catch-all for anything else
- **Observing** the full request lifecycle in a structured wire log
- **Protecting** the pipeline with prompt injection detection

All advanced features are disabled by default and enabled via config flags.

---

## Hybrid Routing

Four tiers with graceful degradation at every level.

**Tier 0 — Session Cache (instant):** Once a routing decision is made for a conversation it's cached. Subsequent turns skip the pipeline entirely.

**Tier 1 — Z-Commands (instant):** User-level routing overrides via `z:` prefix. Absolute priority.

**Tier 2 — Agentic Scorer (instant):** Regex scorer that flags tool-use intent before the classifier runs.

**Tier 3 — Embedding Classifier (~50ms):** Pre-computed centroid vectors classify prompts into simple, complex, code, or creative via cosine similarity. Run `beigebox build-centroids` once, or trigger it from the Config tab in the web UI.

**Tier 4 — Decision LLM (~500ms):** Small fast model for borderline cases the classifier can't confidently resolve.

```
Session cache hit?   → Use cached model. Done.
Z-command found?     → Use it. Done.
Agentic scorer       → Log if flagged. Continue.
Centroids loaded?    → Run embedding classifier.
  Clear result?      → Route. Cache. Done.
  Borderline?        → Fall through to Decision LLM.
Decision LLM on?     → Run it. Route. Cache.
Nothing matched?     → Use default model. Still works.
```

---

## Z-Commands

Prefix any message with `z:` to bypass routing logic:

```
z: simple          force fast model
z: complex         force large model
z: code            force code model
z: (model:tag)     force exact model

z: search          force web search
z: memory          search past conversations (RAG)
z: calc (expr)     evaluate math expression
z: time            current date/time
z: sysinfo         system resource stats

z: complex,search  chain directives
z: help            list everything
```

---

## Operator Agent

`beigebox operator` launches a LangChain ReAct agent with web search, web scrape, conversation search, database queries, and an allowlisted shell. In Docker the shell routes through the hardened `bb` busybox wrapper. Also available in the web UI Operator tab.

```bash
beigebox operator
beigebox op "what did we discuss about routing last week"
```

---

## Observability

**Wire Tap** — Structured JSONL log of every message and forwarded request. Filterable by role and direction. Filters persist in localStorage. Live mode polls every 2s. Also queryable via `beigebox tap`.

**Flight Recorder** — Per-request lifecycle timelines in a ring buffer. Per-stage timing with latency bars in the web UI.

**Conversation Replay** — Reconstruct any conversation with full routing context: model, why it was chosen, tools invoked, backend used, cost per message.

**Semantic Map** — Topic cluster map for any conversation via ChromaDB pairwise cosine similarity.

**Model Performance** — Per-model avg / p50 / p95 latency, request counts, total cost. In `beigebox flash` and the web UI dashboard.

---

## Security

**Prompt Injection Detection** — Pre-request hook scanning for boundary breaking, role overrides, DAN/jailbreak patterns, system prompt extraction, delimiter injection, encoding obfuscation, and prompt chaining.

Two modes: `flag` (annotate and log, let through) or `block` (return refusal, halt pipeline).

```yaml
hooks:
  - name: prompt_injection
    path: ./hooks/prompt_injection.py
    enabled: true
    mode: flag        # or "block"
    score_threshold: 2
```

---

## Web UI

Single-file, no build step, no external JS dependencies. Served at `http://localhost:8000`.

| Tab | Key | Contents |
|---|---|---|
| Dashboard | 1 | Stats cards, subsystem health, backends, cost charts, model performance |
| Chat | 2 | Streaming chat, model selector, z-command hint, history persists across tab switches |
| Conversations | 3 | Semantic search grouped by conversation, replay, per-message forking |
| Flight Recorder | 4 | Request timelines with latency bars |
| Tap | 5 | Wire log, role/direction filters (persisted), live mode |
| Operator | 6 | ReAct agent REPL |
| Config | 7 | Editable settings, Save & Apply, Build Centroids |

**Vi mode** — Disabled by default. Zero bytes loaded when off. Toggle via the π button (bottom-left) or `web_ui_vi_mode: true` in `runtime_config.yaml`.

**Palette** — Set `web_ui_palette` in runtime config or the Config tab: `default`, `phosphor`, `cobalt`, `sakura`, `slate`.

**Forking** — Every message in replay view has a `⑂` button. Branches the conversation from that point into a new ID.

---

## Project Structure

```
beigebox/
  README.md
  config.yaml                    main configuration
  runtime_config.yaml            hot-reloaded overrides, no restart needed
  pyproject.toml
  requirements.txt

  beigebox/
    cli.py                       CLI entry point, phreaker command names
    main.py                      FastAPI app, all endpoints, catch-all passthrough
    proxy.py                     request interception, hybrid routing, block pipeline
    config.py                    config loader, runtime hot-reload
    wiretap.py                   structured JSONL wire log
    summarizer.py                auto-summarization, context window management
    costs.py                     cost aggregation queries
    flight_recorder.py           in-memory request timeline ring buffer
    replay.py                    conversation replay with routing context
    semantic_map.py              topic clustering via ChromaDB
    orchestrator.py              parallel LLM task spawner

    agents/
      decision.py                Decision LLM — Tier 4
      embedding_classifier.py    centroid-based classifier — Tier 3
      agentic_scorer.py          keyword intent pre-filter — Tier 2
      zcommand.py                z-command parser — Tier 1
      operator.py                LangChain ReAct agent

    backends/
      base.py                    BackendResponse dataclass, BaseBackend ABC
      ollama.py                  Ollama backend
      openrouter.py              OpenRouter backend, streaming cost capture
      router.py                  priority-based multi-backend router

    storage/
      sqlite_store.py            conversations, messages, latency, performance queries
      vector_store.py            ChromaDB wrapper, grouped semantic search
      models.py                  Message and Conversation dataclasses

    tools/
      registry.py
      web_search.py
      web_scraper.py
      calculator.py
      datetime_tool.py
      system_info.py             respects operator.shell_binary — bb in Docker
      memory.py
      notifier.py

    web/
      index.html                 single-file web UI, all tabs, no build step
      vi.js                      vi mode, injected only when enabled

  hooks/
    prompt_injection.py          prompt injection detection hook

  scripts/
    export_conversations.py
    migrate_open_webui.py
    search_conversations.py

  docker/
    Dockerfile                   restricted busybox bb wrapper, non-root appuser
    config.docker.yaml
    docker-compose.yaml
    smoke.sh                     full stack validation, 12 test sections

  2600/                          design docs and session archives

  tests/
```

---

## API Endpoints

### OpenAI-compatible (proxied and logged)

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Full routing pipeline, streaming + non-streaming |
| `/v1/models` | GET | Model list from all backends |
| `/v1/embeddings` | POST | Forwarded and logged |
| `/v1/completions` | POST | Legacy completions, forwarded |
| `/v1/audio/transcriptions` | POST | STT — forwarded to configured voice service |
| `/v1/audio/speech` | POST | TTS — forwarded to configured voice service |

### Ollama-native (forwarded and logged)

`/api/tags` `/api/chat` `/api/generate` `/api/pull` `/api/embed` `/api/show` `/api/ps` `/api/version` — all forwarded transparently to the backend.

### BeigeBox

| Endpoint | Method | Description |
|---|---|---|
| `/beigebox/health` | GET | Health check |
| `/api/v1/info` | GET | Version and feature flags |
| `/api/v1/config` | GET / POST | Read config / save runtime settings |
| `/api/v1/status` | GET | Subsystem status |
| `/api/v1/stats` | GET | Conversation and token stats |
| `/api/v1/costs` | GET | Cost breakdown, `?days=30` |
| `/api/v1/model-performance` | GET | Latency percentiles by model |
| `/api/v1/tap` | GET | Wire log with filters |
| `/api/v1/search` | GET | Semantic search grouped by conversation |
| `/api/v1/flight-recorder` | GET | Request timelines |
| `/api/v1/conversation/{id}/replay` | GET | Replay with routing context |
| `/api/v1/conversation/{id}/fork` | POST | Fork conversation at message N |
| `/api/v1/build-centroids` | POST | Rebuild embedding classifier centroids |
| `/api/v1/operator` | POST | Run Operator agent |
| `/{path:path}` | ANY | Catch-all — forwards unknown paths to backend |

---

## CLI Commands

```
PHREAKER        STANDARD         WHAT IT DOES
--------        --------         ----------------------------------
dial            start, up        Start the proxy server
tap             log, tail        Live wiretap
ring            status, ping     Ping a running instance
sweep           search, find     Semantic search over conversations
dump            export           Export conversations to JSON
flash           info, stats      Stats, config, costs, model performance
tone            banner           Print the banner
build-centroids centroids        Generate embedding classifier centroids
operator        op               Launch the Operator agent
setup           install, pull    Pull required Ollama models
```

---

## Configuration

`config.yaml` is the main config. `runtime_config.yaml` is hot-reloaded on every request — no restart needed. Both are editable from the web UI Config tab.

### Feature flags (all disabled by default)

```yaml
backends_enabled: false

cost_tracking:
  enabled: false

auto_summarization:
  enabled: false
  token_budget: 3000
  summary_model: "llama3.2:3b"
  keep_last: 4

flight_recorder:
  enabled: false

conversation_replay:
  enabled: false

semantic_map:
  enabled: false

orchestrator:
  enabled: false

voice:
  enabled: false
  stt_url: ""
  tts_url: ""

hooks:
  - name: prompt_injection
    enabled: false
    mode: flag        # flag | block
    score_threshold: 2
```

---

## Docker Quickstart

```bash
cd docker
docker compose up -d
```

Pulls `llama3.2:3b` and `nomic-embed-text` on first start. Web UI at `http://localhost:8000`. Point Open WebUI at `http://localhost:8000/v1` with any non-empty API key.

```bash
./smoke.sh    # validate the full stack
```

---

## Testing

```bash
pytest tests/ -v

# Core suite, no external dependencies
pytest tests/test_storage.py tests/test_proxy.py tests/test_hooks.py \
       tests/test_costs.py tests/test_zcommand.py tests/test_v08.py -v
```

---

## Roadmap

### Done — v0.9.0

- [x] OpenAI-compatible proxy, transparent streaming, SQLite + ChromaDB storage
- [x] Four-tier hybrid routing — session cache, z-commands, embedding classifier, decision LLM
- [x] Multi-backend router, Ollama → OpenRouter failover
- [x] Cost tracking, streaming and non-streaming
- [x] Streaming latency tracking, model performance dashboard
- [x] Auto-summarization for context window management
- [x] All known OpenAI and Ollama endpoints proxied and logged
- [x] Catch-all passthrough for any unknown endpoint
- [x] LangChain ReAct operator agent
- [x] Parallel orchestrator
- [x] Flight recorder, conversation replay, semantic map
- [x] Wire tap with persistent filters and live mode
- [x] Prompt injection detection, flag and block modes
- [x] Single-file web UI — 7 tabs, editable config, persistent chat history and tap filters
- [x] Vi mode (zero bytes when disabled), palette themes, conversation forking
- [x] Busybox bb shell hardening in Docker
- [x] 68 tests, smoke test covering all endpoints and restart resilience

### v1.0 and beyond

- [ ] Voice — Whisper STT, push-to-talk, Kokoro TTS, lazy-loaded JS, disabled by default
- [ ] Conversation export to fine-tuning formats (JSONL, Alpaca, ShareGPT)
- [ ] Multi-model voting / ensemble responses
- [ ] Web UI mobile layout

---

## Contributing

By contributing you agree your work will be licensed under AGPLv3, and you grant the maintainer(s) a perpetual license to sub-license under alternative commercial terms.

---

## License

AGPLv3. Enterprise use without copyleft — reach out.

If you're making money off it, holler first. Four dogs, two kids, they eat a lot.

Otherwise free for everyone not making a buck from it. PRs and issues welcome if you've got something that fits the style.

---

*BeigeBox — because the most interesting box on the network is the one nobody knows is there.*
