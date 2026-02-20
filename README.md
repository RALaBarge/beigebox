# BeigeBox

**Tap the line. Control the carrier.**

A transparent middleware proxy for local LLM stacks. Sits between your frontend (Open WebUI, etc.) and your backend (Ollama, llama.cpp, etc.), intercepting and storing every conversation while providing intelligent routing, extensible tooling, and user-level overrides.

```
+---------------+         +--------------------------------------+         +---------------+
|               |  HTTP   |            BEIGEBOX                  |  HTTP   |               |
|  Open WebUI   | ------->|                                      | ------- |  Ollama /     |
|  (Frontend)   |<------- |  FastAPI Proxy                       |<------- |  llama.cpp    |
|               |         |                                      |         |  (Backend)    |
|  Port 3000    |         |  +------ Hybrid Router -----------+  |         +---------------+
+---------------+         |  | 0. Session Cache  (instant)    |  |
                          |  | 1. Z-Commands     (instant)    |  |         +---------------+
                          |  | 2. Agentic Scorer (instant)    |  |         |  OpenRouter   |
                          |  | 3. Embedding Class (~50ms)     |  |  HTTP   |  (Fallback)   |
                          |  | 4. Decision LLM   (~500ms-2s)  |  | ------->|               |
                          |  +---------------------------------+ |         |  Priority 2   |
                          |                                      |         +---------------+
                          |  +------ Multi-Backend Router -----+ |
                          |  | Priority-based fallback          | |
                          |  | Ollama (local) -> OpenRouter     | |
                          |  | Cost tracking per request        | |
                          |  +---------------------------------+ |
                          |                                      |
                          |  +----------+  +-------------------+ |
                          |  | SQLite   |  | ChromaDB          | |
                          |  | (raw     |  | (vector           | |
                          |  |  convos) |  |  embeddings)      | |
                          |  +----------+  +-------------------+ |
                          |                                      |
                          |  +------ Tool Registry -----------+  |
                          |  | Web Search  | Calculator        | |
                          |  | Web Scraper | DateTime          | |
                          |  | Memory/RAG  | System Info       | |
                          |  | Notifier    | (extensible)      | |
                          |  +------------------------------- +  |
                          |                                      |
                          |  +------ Operator Agent ----------+  |
                          |  | LangChain ReAct agent          |  |
                          |  | + Parallel Orchestrator (v0.6) |  |
                          |  +---------------------------------+ |
                          |                                      |
                          |  +------ Observability (v0.6) ----+  |
                          |  | Flight Recorder (req timelines) |  |
                          |  | Conversation Replay (routing)   |  |
                          |  | Semantic Map (topic clusters)   |  |
                          |  +---------------------------------+ |
                          |                                      |
                          |  +------ Web UI (v0.7) -----------+  |
                          |  | Single-file, no dependencies    |  |
                          |  | Dashboard, Chat, Flight, Op     |  |
                          |  | Vi mode (runtime toggle)        |  |
                          |  +---------------------------------+ |
                          |                          Port 8000   |
                          +--------------------------------------+
```

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Architecture](#architecture)
3. [Hybrid Routing](#hybrid-routing)
4. [Multi-Backend Router](#multi-backend-router)
5. [Z-Commands](#z-commands)
6. [Operator Agent](#operator-agent)
7. [Observability](#observability)
8. [Web UI](#web-ui)
9. [TUI](#tui)
10. [Project Structure](#project-structure)
11. [Core Components](#core-components)
12. [API Endpoints](#api-endpoints)
13. [CLI Commands](#cli-commands)
14. [Configuration](#configuration)
15. [Setup and Installation](#setup-and-installation)
16. [Docker Quickstart](#docker-quickstart)
17. [Usage](#usage)
18. [Testing](#testing)
19. [Roadmap](#roadmap)

---

## What It Does

BeigeBox is a proxy that makes your local LLM stack smarter without changing anything about how you use it.

Your frontend thinks it is talking to Ollama. Ollama thinks it is getting requests from your frontend. BeigeBox sits in the middle, transparently intercepting every message to:

- **Store** every conversation in portable SQLite + semantic ChromaDB (you own the data, not the frontend)
- **Route** requests to the right model based on complexity
- **Fallback** across multiple backends if Ollama is down
- **Track costs** for API backends
- **Augment** requests with tool output before they hit the LLM
- **Override** any decision with user-level z-commands
- **Observe** the full lifecycle of every request
- **Persist** routing decisions within a conversation
- **Browse** everything through a web UI or TUI with full feature parity

Everything degrades gracefully. Every v0.6 feature is disabled by default and enabled via config flags.

---

## Architecture

### Four Surfaces, One Data Layer

Every feature is accessible through all four interfaces:

| Surface | Entry | Description |
|---|---|---|
| Web UI | http://localhost:8000 | Browser-based, no dependencies |
| TUI | beigebox jack | Terminal UI, lavender palette |
| CLI | beigebox (command) | Phreaker-named shell commands |
| API | curl http://localhost:8000/api/v1/... | Direct REST |

### Dual Storage

- **SQLite**: Source of truth. Every message, timestamp, model, token count, cost.
- **ChromaDB**: Semantic search over conversation history. Embeddings generated asynchronously.

Both use nomic-embed-text for embeddings, running locally via Ollama.

---

## Hybrid Routing

Four-tier system with graceful degradation at every level.

**Tier 0 - Session Cache (instant):** Once a routing decision is made for a conversation, it is cached. Subsequent messages skip the routing pipeline entirely. TTL configurable (default 30 minutes).

**Tier 1 - Z-Commands (instant):** User-level overrides via z: prefix. Absolute priority.

**Tier 2 - Agentic Scorer (instant):** Lightweight keyword scorer that flags tool-use intent before the embedding classifier runs.

**Tier 3 - Embedding Classifier (~50ms):** Pre-computed centroid vectors classify prompts into simple, complex, code, or creative using cosine similarity. Run beigebox build-centroids once to generate vectors.

**Tier 4 - Decision LLM (~500ms-2s):** Small fast model for borderline cases. Outputs structured JSON: route, tools needed, confidence.

```
Session cache hit?  -> Use cached model. Done.
Z-command found?    -> Use it. Done.
Agentic scorer      -> Log if flagged. Continue.
Centroids loaded?   -> Run embedding classifier
  Clear result?     -> Route accordingly. Cache. Done.
  Borderline?       -> Fall through to decision LLM
Decision LLM on?    -> Run it. Route and augment. Cache.
Nothing worked?     -> Use default model. Still works.
```

---

## Multi-Backend Router

*New in v0.6.*

When backends_enabled: true, routes across multiple backends with automatic failover. Ollama first (free), OpenRouter second (cost tracked), graceful error if all fail. Transparent to clients.

---

## Z-Commands

Override any routing decision by prefixing your message with z:

```
z: simple       force fast model
z: complex      force large model
z: code         force code model
z: creative     force creative model
z: (model)      force exact model by name:tag

z: search       force web search augmentation
z: memory       search past conversations (RAG)
z: calc (expr)  evaluate math expression
z: time         get current date/time
z: sysinfo      get system resource stats

z: complex,search What is happening in AI research?

z: help         show all z-commands
```

---

## Operator Agent

beigebox operator launches a LangChain ReAct agent with five tools: web_search, web_scrape, conversation_search, database_query, and allowlisted shell. When orchestrator.enabled: true, also has parallel_orchestrator for divide-and-conquer tasks.

```bash
beigebox operator
beigebox op "summarise everything we discussed about docker last week"
```

---

## Observability

*New in v0.6.*

**Flight Recorder:** Per-request lifecycle timelines in an in-memory ring buffer. Per-stage timing, latency breakdown. Available in web UI, TUI tab 3, and API.

**Conversation Replay:** Reconstruct any conversation with full routing context - model used, why, tools invoked, backend, cost per message.

**Semantic Map:** Topic cluster map for any conversation using ChromaDB embeddings. Pairwise cosine similarity, connected-component clusters.

---

## Web UI

*New in v0.7.* Single-file, no dependencies, served at http://localhost:8000.

| Tab | Key | Contents |
|---|---|---|
| Dashboard | 1 | Stats cards, subsystem health, backends |
| Chat | 2 | Streaming chat, model selector, z-command hint |
| Conversations | 3 | Semantic search + conversation replay |
| Flight Recorder | 4 | Request timelines with latency bars |
| Operator | 5 | LangChain ReAct agent REPL |
| Config | 6 | Live config and runtime overrides |

### Vi Mode

Disabled by default. Loads zero JavaScript when disabled - not just toggled off, completely absent from the page.

Enable by clicking the pi symbol in the bottom-right corner, or set web_ui_vi_mode: true in runtime_config.yaml. Toggle is instant, no reload needed, persists to runtime_config.yaml.

Bindings: hjkl, w/b, 0/$, G/gg, i/I/a/A/o/O, dd/yy/p, x, u undo, /n search, Escape to normal mode. Mode indicator shows -- NORMAL -- or -- INSERT --.

---

## TUI

beigebox jack launches the terminal UI. Three tabs:

| Tab | Key | Contents |
|---|---|---|
| Config | 1 | Live config.yaml + runtime overrides |
| Tap | 2 | Live wire feed |
| Flight | 3 | Recent flight records, auto-polls every 2s |

Press r to refresh all panels. Press q to disconnect.

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
│   ├── proxy.py                   # Request interception, hybrid routing
│   ├── config.py                  # Config loader + runtime write support
│   ├── wiretap.py                 # Structured JSONL wire log
│   ├── agents/
│   │   ├── decision.py            # Decision LLM (Tier 4)
│   │   ├── embedding_classifier.py
│   │   ├── zcommand.py
│   │   ├── agentic_scorer.py
│   │   ├── operator.py
│   │   └── centroids/
│   ├── backends/                  # v0.6: Multi-backend routing
│   │   ├── base.py
│   │   ├── ollama.py
│   │   ├── openrouter.py
│   │   └── router.py
│   ├── storage/
│   │   ├── sqlite_store.py
│   │   ├── vector_store.py
│   │   └── models.py
│   ├── tools/
│   │   ├── registry.py
│   │   ├── web_search.py
│   │   ├── web_scraper.py
│   │   ├── calculator.py
│   │   ├── datetime_tool.py
│   │   ├── system_info.py
│   │   ├── memory.py
│   │   └── notifier.py
│   ├── tui/
│   │   ├── app.py
│   │   ├── styles/main.tcss
│   │   └── screens/
│   │       ├── base.py
│   │       ├── config.py
│   │       ├── tap.py
│   │       └── flight.py          # v0.7: Flight recorder TUI tab
│   ├── web/
│   │   ├── index.html             # v0.7: Single-file web UI
│   │   └── vi.js                  # v0.7: Vi mode (loaded only when enabled)
│   ├── costs.py                   # v0.6
│   ├── flight_recorder.py         # v0.6
│   ├── replay.py                  # v0.6
│   ├── semantic_map.py            # v0.6
│   └── orchestrator.py            # v0.6
├── hooks/
├── scripts/
├── docker/
├── 2600/                          # Design docs and session notes
│   ├── session-feb20-2026.md      # What was built this session
│   ├── v6Summary.md
│   └── (design docs per feature)
├── tests/
│   ├── (existing test files)
│   └── test_web_ui.py             # v0.7: 23 tests for web UI and vi mode
└── data/                          # Runtime, gitignored
```

---

## API Endpoints

### Core
| Endpoint | Method | Description |
|---|---|---|
| /v1/chat/completions | POST | Chat completion (streaming + non-streaming) |
| /v1/models | GET | List models |

### BeigeBox
| Endpoint | Method | Description |
|---|---|---|
| /beigebox/health | GET | Health check |
| /beigebox/stats | GET | Quick stats |
| /beigebox/search | GET | Semantic search |
| /api/v1/info | GET | Version and feature info |
| /api/v1/config | GET | Config including web_ui runtime state |
| /api/v1/status | GET | Full subsystem status |
| /api/v1/stats | GET | Detailed usage stats |
| /api/v1/tools | GET | Available tools |
| /api/v1/operator | POST | Run the Operator agent |

### v0.6
| Endpoint | Method | Description |
|---|---|---|
| /api/v1/costs | GET | Cost tracking (?days=30) |
| /api/v1/backends | GET | Backend health |
| /api/v1/flight-recorder | GET | Recent flight records |
| /api/v1/flight-recorder/{id} | GET | Detailed timeline |
| /api/v1/conversation/{id}/replay | GET | Conversation replay |
| /api/v1/conversation/{id}/semantic-map | GET | Topic cluster map |
| /api/v1/orchestrator | POST | Parallel LLM tasks |

### v0.7
| Endpoint | Method | Description |
|---|---|---|
| /api/v1/web-ui/toggle-vi-mode | POST | Toggle vi mode, persists to runtime_config |
| / | GET | Web UI |
| /ui | GET | Web UI alias |
| /web/vi.js | GET | Vi mode JS (only fetched when enabled) |

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
flash           info, config     Show stats and config
tone            banner           Print the banner
build-centroids centroids        Generate embedding classifier centroids
operator        op               Launch the Operator agent
setup           init             Interactive first-time setup
```

---

## Configuration

All configuration lives in config.yaml. runtime_config.yaml is hot-reloaded on every request with no restart needed.

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
  web_ui_palette: "default"
```

### v0.6 feature flags (all disabled by default)

```yaml
backends_enabled: false
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

cost_tracking:
  enabled: false

orchestrator:
  enabled: false
  max_parallel_tasks: 5

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
ollama pull (your-preferred-model)
```

Edit config.yaml to set backend.url and backend.default_model, then:

```bash
beigebox dial
```

Web UI at http://localhost:8000. Point Open WebUI at http://localhost:8000/v1 with any non-empty API key.

---

## Docker Quickstart

```bash
cd docker
docker compose up -d
```

---

## Usage

```bash
# Web UI
open http://localhost:8000

# TUI
beigebox jack

# Watch the wire
beigebox tap

# Search conversations
beigebox sweep "docker networking"

# Operator agent
beigebox op "what did we discuss about authentication last week?"

# Build routing centroids (one-time)
beigebox build-centroids
```

Z-commands in any chat frontend:
```
z: code Write a binary tree in Rust
z: complex,search Latest news on AI safety?
z: calc 2**16 + 3**10
z: help
```

---

## Testing

```bash
pytest tests/ -v

# v0.6 only
pytest tests/test_backends.py tests/test_costs.py tests/test_flight_recorder.py \
       tests/test_replay.py tests/test_semantic_map.py tests/test_orchestrator.py -v

# v0.7 web UI
pytest tests/test_web_ui.py -v

# No external dependencies needed
pytest tests/test_flight_recorder.py tests/test_costs.py tests/test_replay.py \
       tests/test_web_ui.py -v
```

---

## Roadmap

### Done
- [x] FastAPI proxy with OpenAI-compatible endpoints
- [x] Transparent streaming forwarding
- [x] SQLite + ChromaDB dual storage
- [x] Config-driven architecture
- [x] Tool registry (search, scraper, calc, datetime, sysinfo, memory, notifier)
- [x] Decision LLM routing
- [x] Multi-class embedding classifier (simple / complex / code / creative)
- [x] Z-command user overrides with chaining
- [x] Four-tier hybrid routing with graceful degradation
- [x] Session-aware routing
- [x] Agentic intent scorer
- [x] Web search and RAG augmentation (wired end-to-end)
- [x] Runtime config hot-reload
- [x] Hooks plugin system
- [x] Token tracking
- [x] Synthetic request filtering
- [x] Docker quickstart with health checks
- [x] Wiretap logging with CLI viewer
- [x] Conversation export and Open WebUI migration
- [x] TUI interface (lavender palette)
- [x] Operator agent (LangChain ReAct)

### v0.6 (Done)
- [x] Multi-backend router (Ollama -> OpenRouter fallback)
- [x] Cost tracking for API backends
- [x] Flight recorder (request lifecycle timelines)
- [x] Conversation replay (full routing context)
- [x] Semantic conversation map (topic clustering)
- [x] Parallel orchestrator for Operator
- [x] SQLite schema migrations
- [x] 50+ new tests

### v0.7 (Done)
- [x] TUI crash fix (Textual 8.x tab ID validation)
- [x] Flight recorder TUI screen (tab 3)
- [x] Web UI - single file, no dependencies, full feature parity
- [x] Web UI Chat with streaming and model selector
- [x] Web UI Flight Recorder with expandable timelines
- [x] Web UI Operator REPL
- [x] Vi mode for web UI (dynamic injection, zero bytes when disabled)
- [x] Pi toggle button (persists to runtime_config.yaml)
- [x] update_runtime_config() for programmatic runtime writes
- [x] /api/v1/web-ui/toggle-vi-mode endpoint
- [x] /api/v1/config includes web_ui runtime state
- [x] Slogan updated to "Tap the line. Control the carrier."
- [x] 23 new tests in test_web_ui.py

### Next
- [ ] Busybox in Dockerfile for predictable operator shell surface
- [ ] Streaming cost tracking (OpenRouter generation ID lookup)
- [ ] beigebox flash CLI command for cost summary display
- [ ] Web UI palette themes: dracula, gruvbox, nord, random (infrastructure wired, palettes need implementing)
- [ ] Session cache TTL enforcement (_session_routes in proxy.py grows unbounded)

### Future
- [ ] Conversation summarization for context window management
- [ ] Web dashboard charts (token usage, model distribution, cost over time)
- [ ] Multi-model voting / ensemble responses
- [ ] Voice pipeline integration
- [ ] Fine-tuning data export
- [ ] Agent framework with sandboxed execution
- [ ] Tap filters (--only-zcommands, --model=code, --exclude-internal)
- [ ] Prompt injection detection
- [ ] Conversation forking (z: fork)
- [ ] Model performance dashboard (tokens/sec by model, routing accuracy, cache hit rates)

---

## Notes for Next Session

**Start here:** Upload all project files as a zip, read `2600/session-feb20-2026.md` for a full account of what was built. Test baseline is 133 passing (121 original + 12 new), 11 skipped without chromadb.

**Suggested priorities in order:**

1. **Palette themes** - Infrastructure already wired via web_ui_palette in runtime_config and /api/v1/config. Need CSS variable swap logic in index.html and theme definitions. Random generator should work in HSL space: random hue, saturation 40-60%, lightness 55-75% for accents, derive dim/muted variants by dropping lightness.

2. **Busybox in Dockerfile** - Shell surface hardening for operator.

3. **beigebox flash cost summary** - CLI command hitting /api/v1/costs, formatted table output.

4. **Session cache TTL** - _session_routes in proxy.py grows unbounded. Add TTL eviction on access, check timestamp against routing.session_ttl_seconds.

---

## Contributing

By contributing to this project, you agree that your contributions will be licensed under the project's AGPLv3 license, and you grant the maintainer(s) a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license to incorporate your contributions into the project and sub-license them under alternative commercial terms.

---

## License

Note for Enterprises: This project is licensed under AGPLv3 to keep it free for the community. If you are a high-revenue carbon-based lifeform, synthetic or otherwise, and require a non-copyleft license, please contact me.

If you are gonna make money off of it, you gotta hollar at me first. I have got 4 dogs and 2 kids and they eat a lot.

Otherwise, free to use for everyone not making a buck from it. If you think of something cool or have code that matches the style, please make a PR or an Issue.

---

*BeigeBox - because the most interesting box on the network is the one nobody knows is there.*
