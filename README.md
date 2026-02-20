# BeigeBox

**Tap the line. Control the carrier.**

A transparent middleware proxy for local LLM stacks. Sits between your frontend (Open WebUI, etc.) and your backend (Ollama, llama.cpp, etc.), intercepting and storing every conversation while providing intelligent routing, extensible tooling, observability, and security.

```
+---------------+         +--------------------------------------+         +---------------+
|               |  HTTP   |            BEIGEBOX                  |  HTTP   |               |
|  Open WebUI   | ------->|                                      | ------> |  Ollama /     |
|  (Frontend)   |<------- |  FastAPI Proxy · Port 8000           |<------- |  llama.cpp    |
|               |         |                                      |         |  (Backend)    |
|  Port 3000    |         |  Hybrid Router                       |         +---------------+
+---------------+         |  ├─ 0. Session Cache  (instant)      |
                          |  ├─ 1. Z-Commands     (instant)      |         +---------------+
                          |  ├─ 2. Agentic Scorer (instant)      |         |  OpenRouter   |
                          |  ├─ 3. Embedding Class (~50ms)       |  HTTP   |  (Fallback)   |
                          |  └─ 4. Decision LLM   (~500ms)       | ------> |  Priority 2   |
                          |                                      |         +---------------+
                          |  Multi-Backend Router                |
                          |  SQLite · ChromaDB                   |
                          |  Tool Registry · Operator Agent      |
                          |  Observability · Security            |
                          +--------------------------------------+
```

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Hybrid Routing](#hybrid-routing)
3. [Multi-Backend Router](#multi-backend-router)
4. [Z-Commands](#z-commands)
5. [Operator Agent](#operator-agent)
6. [Observability](#observability)
7. [Security](#security)
8. [Web UI](#web-ui)
9. [TUI](#tui)
10. [Project Structure](#project-structure)
11. [API Endpoints](#api-endpoints)
12. [CLI Commands](#cli-commands)
13. [Configuration](#configuration)
14. [Setup and Installation](#setup-and-installation)
15. [Docker Quickstart](#docker-quickstart)
16. [Testing](#testing)
17. [Roadmap](#roadmap)

---

## What It Does

BeigeBox is a proxy that makes your local LLM stack smarter without changing anything about how you use it.

Your frontend thinks it is talking to Ollama. Ollama thinks it is getting requests from your frontend. BeigeBox sits in the middle, transparently intercepting every message to:

- **Store** every conversation in portable SQLite + semantic ChromaDB (you own the data)
- **Route** requests to the right model based on query complexity
- **Fallback** across multiple backends with priority-based failover
- **Track costs** for API backends, including streaming requests
- **Augment** requests with tool output before they hit the LLM
- **Override** any decision with user-level z-commands
- **Observe** the full lifecycle of every request
- **Protect** the pipeline with prompt injection detection
- **Analyze** model performance with latency percentiles
- **Browse** everything through a web UI or TUI

Everything degrades gracefully. Advanced features are disabled by default and enabled via config flags.

---

## Hybrid Routing

Four-tier system with graceful degradation at every level.

**Tier 0 — Session Cache (instant):** Once a routing decision is made for a conversation, it is cached for the TTL window. Subsequent messages skip the routing pipeline entirely.

**Tier 1 — Z-Commands (instant):** User-level overrides via `z:` prefix. Absolute priority over everything.

**Tier 2 — Agentic Scorer (instant):** Lightweight keyword scorer that flags tool-use intent before the embedding classifier runs.

**Tier 3 — Embedding Classifier (~50ms):** Pre-computed centroid vectors classify prompts into simple, complex, code, or creative using cosine similarity. Run `beigebox build-centroids` once to generate.

**Tier 4 — Decision LLM (~500ms):** Small fast model for borderline cases. Outputs structured JSON: route, tools needed, confidence.

```
Session cache hit?   → Use cached model. Done.
Z-command found?     → Use it. Done.
Agentic scorer       → Log if flagged. Continue.
Centroids loaded?    → Run embedding classifier.
  Clear result?      → Route accordingly. Cache. Done.
  Borderline?        → Fall through to decision LLM.
Decision LLM on?     → Run it. Route and augment. Cache.
Nothing matched?     → Use default model. Still works.
```

---

## Multi-Backend Router

When `backends_enabled: true`, routes across multiple backends with automatic failover. Ollama first (free), OpenRouter second (cost tracked on both streaming and non-streaming), graceful SSE error if all fail. Transparent to clients.

Cost tracking uses OpenRouter's `stream_options: {include_usage: true}` to capture token counts from the final SSE chunk on streaming requests — previously untracked.

---

## Z-Commands

Override any routing decision by prefixing your message with `z:`:

```
z: simple          force fast model
z: complex         force large model
z: code            force code model
z: creative        force creative model
z: (model)         force exact model by name:tag

z: search          force web search augmentation
z: memory          search past conversations (RAG)
z: calc (expr)     evaluate math expression
z: time            get current date/time
z: sysinfo         get system resource stats

z: complex,search  chain multiple directives

z: help            show all z-commands
```

---

## Operator Agent

`beigebox operator` launches a LangChain ReAct agent with tools: web_search, web_scrape, conversation_search, database_query, and allowlisted shell. The shell tool respects `operator.shell_binary` in config — in Docker this routes through the busybox `bb` wrapper rather than `/bin/sh`.

When `orchestrator.enabled: true`, the operator also gains `parallel_orchestrator` for divide-and-conquer tasks.

```bash
beigebox operator
beigebox op "summarise everything we discussed about docker last week"
```

---

## Observability

**Flight Recorder:** Per-request lifecycle timelines in an in-memory ring buffer. Per-stage timing with latency breakdown bars. Available in the web UI (tab 4), TUI (tab 3), and API.

**Conversation Replay:** Reconstruct any conversation with full routing context — model used, why, tools invoked, backend, cost per message.

**Semantic Map:** Topic cluster map for any conversation using ChromaDB embeddings. Pairwise cosine similarity, connected-component clusters.

**Model Performance Dashboard:** Per-model latency stats (avg, p50, p95) computed from stored `latency_ms` values. Request counts, average tokens, total cost. Rendered as a dual-bar chart (avg + p95 ghost) in the web UI dashboard. Data is recorded on non-streaming requests via the multi-backend router.

**Wire Tap:** Full structured JSONL log of every message through the proxy. Filterable by role and direction in both the web UI (tab 5) and `beigebox tap`. Live mode polls every 2 seconds.

---

## Security

**Prompt Injection Detection:** Pre-request hook that scans user messages for structural injection patterns — boundary breaking, role override, DAN/jailbreak personas, system prompt extraction, delimiter injection, encoding obfuscation, and prompt chaining. Seven weighted patterns with a configurable score threshold.

Two modes:

- `flag` — annotates the request with `_bb_injection_flag`, logs to wire, lets it through. Default.
- `block` — returns a refusal response and halts the pipeline. Visible in flight recorder and wiretap.

Enable in `config.yaml`:
```yaml
hooks:
  - name: prompt_injection
    path: ./hooks/prompt_injection.py
    enabled: true
    mode: flag          # or "block"
    score_threshold: 2
```

---

## Web UI

Single-file, no dependencies, served at `http://localhost:8000`.

| Tab | Key | Contents |
|---|---|---|
| Dashboard | 1 | Stats cards, subsystem health, backends, cost charts, model performance |
| Chat | 2 | Streaming chat, model selector, z-command hint |
| Conversations | 3 | Semantic search, replay, conversation forking |
| Flight Recorder | 4 | Request timelines with latency bars |
| Tap | 5 | Wire log with role/direction filters, live mode |
| Operator | 6 | LangChain ReAct agent REPL |
| Config | 7 | Live config and runtime overrides |

### Conversation Forking

Every message row in the replay view has a `⑂` fork button. Creates a new conversation ID with messages 0–N copied in. The top-level `⑂ Fork` button copies the full thread. Returns the new conversation ID immediately.

### Vi Mode

Disabled by default. Loads zero JavaScript when disabled — not just toggled off, literally absent from the page. Toggle via the π button bottom-right or `web_ui_vi_mode: true` in `runtime_config.yaml`. Persists across reloads.

Bindings: `hjkl`, `w/b`, `0/$`, `G/gg`, `i/I/a/A/o/O`, `dd/yy/p`, `x`, `u` undo, `/n` search.

### Palette Themes

Set `web_ui_palette` in `runtime_config.yaml`: `default`, `dracula`, `gruvbox`, `nord`, or `random`. Random generates a harmonious HSL palette at runtime.

---

## TUI

`beigebox jack` launches the terminal UI.

| Tab | Key | Contents |
|---|---|---|
| Config | 1 | Live config.yaml + runtime overrides |
| Tap | 2 | Live wire feed with auto-poll |
| Flight | 3 | Recent flight records, auto-polls every 2s |

Press `r` to refresh. Press `q` to disconnect.

---

## Project Structure

```
beigebox/
├── README.md
├── config.yaml                    # All runtime configuration
├── runtime_config.yaml            # Hot-reloaded overrides (no restart needed)
├── beigebox/
│   ├── cli.py                     # CLI commands (phreaker names)
│   ├── main.py                    # FastAPI app, all endpoints
│   ├── proxy.py                   # Request interception, hybrid routing, block pipeline
│   ├── config.py                  # Config loader + runtime write support
│   ├── wiretap.py                 # Structured JSONL wire log
│   ├── costs.py                   # Cost aggregation queries
│   ├── flight_recorder.py         # In-memory request timeline ring buffer
│   ├── replay.py                  # Conversation replay with routing context
│   ├── semantic_map.py            # Topic clustering via ChromaDB
│   ├── orchestrator.py            # Parallel LLM task spawner
│   ├── agents/
│   │   ├── decision.py            # Decision LLM (Tier 4)
│   │   ├── embedding_classifier.py # Centroid-based fast classifier (Tier 3)
│   │   ├── zcommand.py            # Z-command parser
│   │   ├── agentic_scorer.py      # Keyword intent pre-filter (Tier 2)
│   │   └── operator.py            # LangChain ReAct agent TUI screen
│   ├── backends/
│   │   ├── base.py                # BackendResponse dataclass, BaseBackend ABC
│   │   ├── ollama.py              # Ollama backend
│   │   ├── openrouter.py          # OpenRouter backend (streaming cost capture)
│   │   └── router.py              # Priority-based multi-backend router
│   ├── storage/
│   │   ├── sqlite_store.py        # SQLite store (latency_ms, fork, perf queries)
│   │   ├── vector_store.py        # ChromaDB wrapper
│   │   └── models.py              # Message / Conversation dataclasses
│   ├── tools/
│   │   ├── registry.py
│   │   ├── web_search.py
│   │   ├── web_scraper.py
│   │   ├── calculator.py
│   │   ├── datetime_tool.py
│   │   ├── system_info.py         # Respects operator.shell_binary config
│   │   ├── memory.py
│   │   └── notifier.py
│   ├── tui/
│   │   ├── app.py
│   │   ├── styles/main.tcss
│   │   └── screens/
│   │       ├── base.py
│   │       ├── config.py
│   │       ├── tap.py
│   │       └── flight.py
│   └── web/
│       ├── index.html             # Single-file web UI (all tabs, charts, vi mode)
│       └── vi.js                  # Vi mode (loaded only when enabled)
├── hooks/
│   └── prompt_injection.py        # Prompt injection detection hook
├── scripts/
│   ├── export_conversations.py
│   ├── migrate_open_webui.py
│   └── search_conversations.py
├── docker/
│   ├── Dockerfile                 # Busybox + non-root appuser
│   ├── config.docker.yaml
│   ├── docker-compose.yaml
│   └── smoke.sh
├── 2600/                          # Design docs and session archives
└── tests/
```

---

## API Endpoints

### Core
| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completion (streaming + non-streaming) |
| `/v1/models` | GET | List models from all backends |

### Proxy
| Endpoint | Method | Description |
|---|---|---|
| `/beigebox/health` | GET | Health check |
| `/beigebox/search` | GET | Semantic search (`?q=&n=`) |
| `/api/v1/info` | GET | Version and feature info |
| `/api/v1/config` | GET | Config including web_ui runtime state |
| `/api/v1/status` | GET | Full subsystem status |
| `/api/v1/stats` | GET | Conversation and token stats |
| `/api/v1/tools` | GET | Available tools |
| `/api/v1/backends` | GET | Backend health and priority |
| `/api/v1/costs` | GET | Cost stats (`?days=30`) |
| `/api/v1/model-performance` | GET | Latency p50/p95, throughput by model (`?days=30`) |
| `/api/v1/tap` | GET | Wire log entries with filters (`?n=50&role=user&dir=inbound`) |
| `/api/v1/flight-recorder` | GET | Recent flight records (`?n=20`) |
| `/api/v1/flight-recorder/{id}` | GET | Detailed timeline for one record |
| `/api/v1/conversation/{id}/replay` | GET | Conversation with full routing context |
| `/api/v1/conversation/{id}/fork` | POST | Fork a conversation (`{"branch_at": N}`) |
| `/api/v1/conversation/{id}/semantic-map` | GET | Topic cluster map |
| `/api/v1/orchestrator` | POST | Parallel LLM tasks |
| `/api/v1/operator` | POST | Run the Operator agent |
| `/api/v1/web-ui/toggle-vi-mode` | POST | Toggle vi mode, persists to runtime_config |
| `/` | GET | Web UI |

---

## CLI Commands

```
PHREAKER        STANDARD         WHAT IT DOES
--------        --------         ----------------------------------
dial            start, up        Start the proxy server
jack            tui, ui          Launch the TUI
tap             log, tail        Live wiretap
ring            status, ping     Ping a running instance
sweep           search, find     Semantic search over conversations
dump            export           Export conversations to JSON
flash           info, config     Show stats, config, and cost summary
tone            banner           Print the banner
build-centroids centroids        Generate embedding classifier centroids
operator        op               Launch the Operator agent
setup           init             Interactive first-time setup
```

---

## Configuration

`config.yaml` is the main config. `runtime_config.yaml` is hot-reloaded on every request with no restart needed.

### runtime_config.yaml keys

```yaml
runtime:
  default_model: ""
  border_threshold: null
  agentic_threshold: null
  force_route: ""
  tools_disabled: []
  system_prompt_prefix: ""
  log_level: ""
  web_ui_vi_mode: false
  web_ui_palette: "default"   # default | dracula | gruvbox | nord | random
```

### Feature flags (all disabled by default)

```yaml
backends_enabled: false
backends:
  - name: local
    url: http://localhost:11434
    provider: ollama
    priority: 1
    timeout: 120
  - name: openrouter
    url: https://openrouter.ai/api/v1
    provider: openrouter
    api_key: "${OPENROUTER_API_KEY}"
    priority: 2
    timeout: 60

cost_tracking:
  enabled: false

orchestrator:
  enabled: false
  max_parallel_tasks: 5
  shell_binary: /bin/sh       # Docker: /usr/local/bin/bb

flight_recorder:
  enabled: false
  max_records: 1000
  retention_hours: 24

conversation_replay:
  enabled: false

semantic_map:
  enabled: false
  similarity_threshold: 0.5
  max_topics: 50

hooks:
  - name: prompt_injection
    path: ./hooks/prompt_injection.py
    enabled: false
    mode: flag          # flag | block
    score_threshold: 2
```

---

## Setup and Installation

```bash
git clone https://github.com/RALaBarge/beigebox.git
cd beigebox
pip install -e .
```

Pull required models:
```bash
ollama pull nomic-embed-text
ollama pull <your-preferred-model>
```

Edit `config.yaml` to set `backend.url` and `backend.default_model`, then:

```bash
beigebox dial
```

Web UI at `http://localhost:8000`. Point Open WebUI at `http://localhost:8000/v1` with any non-empty API key.

---

## Docker Quickstart

```bash
cd docker
docker compose up -d
```

---

## Testing

```bash
pytest tests/ -v

# Feature-specific
pytest tests/test_backends.py tests/test_costs.py tests/test_flight_recorder.py \
       tests/test_replay.py tests/test_semantic_map.py tests/test_orchestrator.py -v

# No external dependencies (runs anywhere)
pytest tests/test_flight_recorder.py tests/test_costs.py tests/test_replay.py \
       tests/test_web_ui.py -v
```

---

## Roadmap

### Done — v0.8.0

**Foundation**
- [x] FastAPI proxy with OpenAI-compatible endpoints
- [x] Transparent streaming forwarding
- [x] SQLite + ChromaDB dual storage
- [x] Token tracking
- [x] Wiretap (structured JSONL log + CLI viewer)
- [x] Runtime config hot-reload
- [x] Hooks plugin system
- [x] Synthetic request filtering
- [x] Docker quickstart with health checks
- [x] Conversation export and Open WebUI migration

**Routing**
- [x] Four-tier hybrid routing (session cache → z-commands → embedding → decision LLM)
- [x] Session-aware routing with TTL eviction
- [x] Agentic intent scorer
- [x] Multi-class embedding classifier (simple / complex / code / creative)
- [x] Decision LLM routing
- [x] Z-command user overrides with chaining
- [x] Web search and RAG augmentation

**Backends**
- [x] Multi-backend router (priority-based, Ollama → OpenRouter fallback)
- [x] Cost tracking — non-streaming
- [x] Cost tracking — streaming (OpenRouter `include_usage`)
- [x] Model performance dashboard (avg / p50 / p95 latency stored per response)

**Operator**
- [x] LangChain ReAct operator agent
- [x] Parallel orchestrator
- [x] Busybox shell hardening (Docker + `shell_binary` config key honoured in `system_info.py`)

**Observability**
- [x] Flight recorder (request lifecycle timelines)
- [x] Conversation replay (full routing context)
- [x] Semantic map (topic clustering)
- [x] Wire tap with role / direction filters (web UI + CLI)

**Security**
- [x] Prompt injection detection hook (flag + block modes, 7 pattern families)
- [x] `_beigebox_block` pipeline short-circuit (streaming + non-streaming)

**Web UI**
- [x] Single-file, no dependencies
- [x] Dashboard with stat cards, subsystem health, cost charts, latency chart
- [x] Chat with streaming and model selector
- [x] Conversations with semantic search, replay, and per-message fork buttons
- [x] Flight Recorder with expandable latency bars
- [x] Tap tab with live mode and role / direction filters
- [x] Operator REPL
- [x] Config view with live runtime state
- [x] Vi mode (dynamic injection, zero bytes when disabled)
- [x] Palette themes (default, dracula, gruvbox, nord, random)

**TUI**
- [x] Lavender palette, three tabs (Config, Tap, Flight)
- [x] Flight recorder screen with auto-poll

---

### Next — v0.9.0

**Streaming latency tracking** — `latency_ms` is currently only recorded for non-streaming responses via the multi-backend router. For streaming, the proxy could record time-to-first-token and total stream duration and store those separately. Would close the gap in the model performance dashboard.

**Auto-summarization** — When a conversation exceeds a configurable token budget, summarise older messages and replace them with the summary to free context window. Needs a summary model config key and a trigger threshold.

**Conversation search improvements** — The current semantic search returns individual messages; it would be more useful to return conversations ranked by relevance and show the best matching excerpt. Requires a two-pass query (message → group by conversation → rank).

**Tap filter persistence** — The web UI tap filters reset on page reload. Saving the last used role/direction/n to localStorage (or runtime_config) would make it more useful as a monitoring tool.

**Test coverage for v0.8 features** — No tests yet for: `fork_conversation`, `get_model_performance`, `/api/v1/tap`, prompt injection hook, streaming cost sentinel. These are the highest-value gaps before cutting a stable release.

---

### Future — v1.0 and beyond

- [ ] Voice pipeline (Whisper STT → LLM → TTS loop)
- [ ] Conversation export to fine-tuning format (JSONL, Alpaca, ShareGPT)
- [ ] Multi-model voting / ensemble responses
- [ ] Plugin system for custom tools (beyond the hooks system)
- [ ] Agent framework with sandboxed execution
- [ ] Web UI mobile layout

---

## Contributing

By contributing to this project, you agree that your contributions will be licensed under the project's AGPLv3 license, and you grant the maintainer(s) a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license to incorporate your contributions into the project and sub-license them under alternative commercial terms.

---

## License

Note for Enterprises: This project is licensed under AGPLv3 to keep it free for the community. If you are a high-revenue carbon-based lifeform, synthetic or otherwise, and require a non-copyleft license, please contact me.

If you are gonna make money off of it, you gotta hollar at me first. I have got 4 dogs and 2 kids and they eat a lot.

Otherwise, free to use for everyone not making a buck from it. If you think of something cool or have code that matches the style, please make a PR or an Issue.

---

*BeigeBox — because the most interesting box on the network is the one nobody knows is there.*
