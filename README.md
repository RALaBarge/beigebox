# BeigeBox

**Tap the line. Own the conversation.**

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
                          |  | Ollama (local) → OpenRouter (API)| |
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
                          |                          Port 8001   |
                          +--------------------------------------+
```

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Architecture](#architecture)
3. [Hybrid Routing](#hybrid-routing)
4. [Multi-Backend Router (v0.6)](#multi-backend-router)
5. [Z-Commands](#z-commands)
6. [Operator Agent](#operator-agent)
7. [Observability (v0.6)](#observability)
8. [Project Structure](#project-structure)
9. [Core Components](#core-components)
10. [API Endpoints](#api-endpoints)
11. [CLI Commands](#cli-commands)
12. [Configuration](#configuration)
13. [Setup and Installation](#setup-and-installation)
14. [Docker Quickstart](#docker-quickstart)
15. [Usage](#usage)
16. [Testing](#testing)
17. [Roadmap](#roadmap)

---

## What It Does

BeigeBox is a proxy that makes your local LLM stack smarter without changing anything about how you use it.

Your frontend thinks it's talking to Ollama. Ollama thinks it's getting requests from your frontend. BeigeBox sits in the middle, transparently intercepting every message to:

- **Store** every conversation in portable SQLite + semantic ChromaDB (you own the data, not the frontend)
- **Route** requests to the right model based on complexity (fast model for simple questions, large model for hard ones, code model for programming, creative model for writing)
- **Fallback** across multiple backends — if Ollama is down, transparently route to OpenRouter or any other OpenAI-compatible API
- **Track costs** for API backends so you know exactly what you're spending per model, per day, per conversation
- **Augment** requests with tool output before they hit the LLM (web search, conversation memory, math, system info)
- **Override** any decision with user-level z-commands when you know better than the router
- **Observe** the full lifecycle of every request — flight recorder timelines, conversation replay with routing context, semantic topic maps
- **Persist** routing decisions within a conversation so the model doesn't switch mid-thread

Everything degrades gracefully. If routing is disabled, BeigeBox is a transparent proxy. If tools are disabled, requests pass through unaugmented. If storage fails, the conversation still works. Every v0.6 feature is disabled by default and enabled via config flags.

---

## Architecture

### Data Flow

```
1. User types message in Open WebUI
2. Open WebUI sends POST /v1/chat/completions to BeigeBox (port 8000)
3. BeigeBox intercepts:
   a. Flight recorder starts timing (if enabled)
   b. Check session cache — same conversation? Use the same model. (instant)
   c. Check for z-command override (instant)
   d. Run agentic scorer — flag tool-intent prompts (instant)
   e. Run embedding classifier (fast path, ~50ms)
   f. If borderline, escalate to decision LLM (slow path, ~500ms-2s)
   g. Execute any forced/decided tools (web search, RAG, etc.)
   h. Log user message to SQLite + embed to ChromaDB
   i. Forward request via multi-backend router (priority fallback)
4. Backend returns response (streamed)
5. BeigeBox intercepts response:
   a. Log assistant message to SQLite + embed to ChromaDB
   b. Track token usage + cost (if API backend)
   c. Flight recorder closes timeline
   d. Stream response back to Open WebUI
6. User sees response (no idea BeigeBox exists)
```

### OpenAI-Compatible API

BeigeBox implements the OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`). This means it works with any frontend or backend that speaks this format — which is virtually everything in the LLM ecosystem. No custom protocols, no vendor lock-in.

### Dual Storage

- **SQLite**: Source of truth. Every message, timestamp, model, token count, cost. Portable single file you can query with standard SQL or export to JSON.
- **ChromaDB**: Semantic search over conversation history. Embeddings generated asynchronously so they never add latency. Enables RAG and the `beigebox sweep` semantic search command.

Both use `nomic-embed-text` for embeddings, which runs locally via Ollama with no external API calls.

---

## Hybrid Routing

BeigeBox routes requests through a four-tier system with graceful degradation at every level:

### Tier 0: Session Cache (instant)

Once a routing decision has been made for a conversation, it's cached. Subsequent messages in the same thread skip the entire routing pipeline and go straight to the same model. TTL is configurable (default 30 minutes). This prevents mid-conversation model switches that would break context and tone.

### Tier 1: Z-Commands (instant)

User-level overrides via `z:` prefix. Absolute priority — bypasses all automated routing. See [Z-Commands](#z-commands) below. Z-command overrides are intentionally not cached, since the user is being explicit.

### Tier 2: Agentic Scorer (instant)

A lightweight keyword scorer that flags prompts with high tool-use intent before the embedding classifier runs. Near-zero cost. High agentic scores are logged to the wiretap and available for future forced-tool routing logic.

### Tier 3: Embedding Classifier (~50ms)

Inspired by [NadirClaw](https://github.com/doramirdor/NadirClaw). Pre-computed centroid vectors classify prompts into one of four routes — simple, complex, code, or creative — using cosine similarity in embedding space. Handles the majority of requests without needing the heavier decision LLM.

Uses the same `nomic-embed-text` model already loaded for ChromaDB — zero new dependencies, zero new models to pull. Run `beigebox build-centroids` once to generate the centroid vectors.

### Tier 4: Decision LLM (~500ms–2s)

A small, fast model reads the prompt and outputs structured JSON: which route to use, whether tools are needed, confidence level. Only called for borderline cases where the embedding classifier isn't confident enough.

### Degradation Path

```
Session cache hit?   → Use cached model. Done.
Z-command found?     → Use it. Done.
Agentic scorer runs  → Log if flagged. Continue.
Centroids loaded?    → Run embedding classifier
  Clear result?      → Route accordingly. Cache result. Done.
  Borderline?        → Fall through to decision LLM
Decision LLM on?     → Run it. Route + augment accordingly. Cache result.
Nothing worked?      → Use default model. Conversation still works.
```

Every tier is independently optional. BeigeBox works as a simple passthrough proxy with everything disabled, and gains intelligence as you enable features.

---

## Multi-Backend Router

*New in v0.6.*

When `backends_enabled: true`, BeigeBox can route requests across multiple backends with automatic failover:

```
Request → MultiBackendRouter
    ├─ Try backend 1 (Ollama, priority 1, local)
    │   ├─ Available? → Use it ($0)
    │   └─ Timeout?   → Try next
    ├─ Try backend 2 (OpenRouter, priority 2, API)
    │   ├─ Available? → Use it (cost tracked)
    │   └─ Failed?    → Try next
    └─ All failed?    → Graceful error
```

Backends are configured in `config.yaml` with priority ordering. Lower priority number = tried first. Each backend has its own timeout. The router is transparent to clients — same request in, same response out.

### Cost Tracking

When `cost_tracking.enabled: true`, BeigeBox logs the cost of every API request to SQLite. Local models are free ($0). OpenRouter costs are extracted from the API response. Query costs via the API:

```
GET /api/v1/costs?days=30
→ total, by_model, by_day, by_conversation
```

---

## Z-Commands

Override any routing decision by prefixing your message with `z:`. The prefix is stripped before the LLM sees your message. All overrides are logged to the wiretap for debugging.

### Routing

```
z: simple    → force fast/simple model
z: complex   → force large/complex model
z: code      → force code model
z: creative  → force creative model
z: <model>   → force exact model by name:tag
```

Aliases: `easy`/`fast` → simple route, `hard`/`large` → complex route, `coding` → code route.

### Tools

```
z: search              → force web search augmentation
z: memory              → search past conversations (RAG)
z: calc <expression>   → evaluate math expression
z: time                → get current date/time
z: sysinfo             → get system resource stats
```

### Chaining

Combine directives with commas:

```
z: complex,search What's the latest news on AI safety?
z: code,memory How did we implement the proxy last time?
```

### Meta

```
z: help    → show all available z-commands (returned directly, doesn't hit LLM)
```

Z-commands are case-insensitive and whitespace-tolerant.

---

## Operator Agent

`beigebox operator` launches an interactive LangChain ReAct agent with access to five tools:

- **web_search** — DuckDuckGo search
- **web_scrape** — fetch and extract content from a URL
- **conversation_search** — semantic search over stored conversations (ChromaDB)
- **database_query** — named queries from config (no raw SQL passthrough)
- **shell** — allowlisted commands only, 15s timeout, pattern blocklist

When `orchestrator.enabled: true`, the Operator also has access to the **parallel_orchestrator** tool — it can spawn multiple LLM tasks concurrently and collect results for divide-and-conquer approaches.

```bash
# Interactive REPL
beigebox operator

# One-shot query
beigebox op "summarise everything we discussed about docker last week"
```

The shell tool only runs binaries explicitly listed in `config.yaml` under `operator.shell_allowlist`. Dangerous patterns (pipes to shell, command substitution, etc.) are blocked regardless of allowlist.

---

## Observability

*New in v0.6.* Three tools for understanding what BeigeBox is doing and why.

### Flight Recorder

When `flight_recorder.enabled: true`, every request gets a detailed timeline showing exactly what happened and how long each stage took:

```
FLIGHT RECORD: a1b2c3d4e5f6
  [     0.0ms] Request Received  (model=llama3.2, tokens=10)
  [     0.1ms] Z-Command Parse  (active=False)
  [     0.3ms] Pre-Hooks
  [    52.1ms] Routing Complete  (model=llama3.2, method=fast_path)
  [    52.5ms] Backend Forward Start
  [  1234.8ms] Backend Response  (backend=local, latency_ms=1182.3, ok=True)
  [  1236.1ms] Response Logged  (tokens=256)
  [  1236.2ms] Complete

  TOTAL: 1236ms
```

In-memory ring buffer (max 1000 records, 24h retention). Query via `GET /api/v1/flight-recorder`.

### Conversation Replay

When `conversation_replay.enabled: true`, reconstruct any conversation with full routing context — which model was used for each message, why it was routed that way, what tools were invoked, which backend served it, and what it cost.

```
GET /api/v1/conversation/{conv_id}/replay
→ timeline with routing method, confidence, tools, backend, cost per message
```

### Semantic Map

When `semantic_map.enabled: true`, generate a topic cluster map for any conversation using the existing ChromaDB embeddings. Computes pairwise cosine similarity between user messages, builds edges above a threshold, and detects connected-component clusters.

```
GET /api/v1/conversation/{conv_id}/semantic-map
→ topics, edges with similarity scores, clusters with cohesion metrics
```

---

## Project Structure

```
beigebox/
├── README.md
├── LICENSE.md                     # AGPLv3
├── config.yaml                    # All runtime configuration
├── runtime_config.yaml            # Hot-reloaded overrides (no restart needed)
├── pyproject.toml                 # Package metadata + CLI entry point
├── requirements.txt
├── setup.sh                       # Interactive setup script
│
├── beigebox/
│   ├── __init__.py
│   ├── __main__.py                # python -m beigebox entry
│   ├── cli.py                     # CLI commands (phreaker names)
│   ├── main.py                    # FastAPI app, lifespan, all endpoints
│   ├── proxy.py                   # Request interception, hybrid routing, forwarding
│   ├── config.py                  # Config loader with hot-reload support
│   ├── wiretap.py                 # Structured JSONL wire log + live viewer
│   │
│   ├── agents/
│   │   ├── decision.py            # Decision LLM (Tier 4 router)
│   │   ├── embedding_classifier.py # Embedding classifier (Tier 3 router)
│   │   ├── zcommand.py            # Z-command parser (Tier 1 overrides)
│   │   ├── agentic_scorer.py      # Agentic intent scorer (Tier 2 pre-filter)
│   │   ├── operator.py            # LangChain ReAct operator agent
│   │   └── centroids/             # Pre-computed centroid .npy files
│   │
│   ├── backends/                  # v0.6: Multi-backend routing
│   │   ├── base.py                # BaseBackend abstraction + BackendResponse
│   │   ├── ollama.py              # Ollama backend (local, $0)
│   │   ├── openrouter.py          # OpenRouter backend (API, cost tracked)
│   │   └── router.py              # MultiBackendRouter (priority fallback)
│   │
│   ├── storage/
│   │   ├── sqlite_store.py        # SQLite storage + schema migrations
│   │   ├── vector_store.py        # ChromaDB embedding storage
│   │   └── models.py              # Data models / schemas
│   │
│   ├── tools/
│   │   ├── registry.py            # Tool registration and dispatch
│   │   ├── web_search.py          # DuckDuckGo search (LangChain)
│   │   ├── web_scraper.py         # URL content extraction
│   │   ├── google_search.py       # Google search (API key optional)
│   │   ├── calculator.py          # Safe math expression evaluator
│   │   ├── datetime_tool.py       # Time, date, timezone tools
│   │   ├── system_info.py         # Host system stats
│   │   ├── memory.py              # Semantic search over past conversations
│   │   └── notifier.py            # Webhook/notification dispatch
│   │
│   ├── tui/
│   │   ├── app.py                 # BeigeBoxApp, SCREEN_REGISTRY
│   │   ├── styles/
│   │   │   └── main.tcss          # Lavender palette styling
│   │   └── screens/
│   │       ├── base.py            # BeigeBoxPane base class
│   │       ├── config.py          # Config panel
│   │       └── tap.py             # Live wire feed panel
│   │
│   ├── costs.py                   # v0.6: Cost tracking queries
│   ├── flight_recorder.py         # v0.6: Request lifecycle timelines
│   ├── replay.py                  # v0.6: Conversation reconstruction
│   ├── semantic_map.py            # v0.6: Topic clustering via embeddings
│   └── orchestrator.py            # v0.6: Parallel LLM task spawning
│
├── hooks/
│   ├── filter_synthetic.py        # Filter synthetic/internal requests
│   └── rag_context.py             # RAG context injection template
│
├── scripts/
│   ├── export_conversations.py    # SQLite → JSON export
│   ├── search_conversations.py    # CLI semantic search
│   └── migrate_open_webui.py      # Import Open WebUI history
│
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yaml        # Full stack deployment
│   └── config.docker.yaml
│
├── 2600/                          # Design docs, research, theorycrafting
│   ├── v6Summary.md               # v0.6 feature roadmap
│   ├── multi-backend-design.md
│   ├── cost-tracking-design.md
│   ├── flight-recorder-design.md
│   ├── conversation-replay-design.md
│   ├── semantic-map-design.md
│   ├── orchestrator-design.md
│   └── todo.md
│
├── tests/
│   ├── test_proxy.py
│   ├── test_storage.py
│   ├── test_tools.py
│   ├── test_decision.py
│   ├── test_hooks.py
│   ├── test_new_tools.py
│   ├── test_zcommand.py
│   ├── test_model_advertising.py
│   ├── test_backends.py           # v0.6
│   ├── test_costs.py              # v0.6
│   ├── test_flight_recorder.py    # v0.6
│   ├── test_replay.py             # v0.6
│   ├── test_semantic_map.py       # v0.6
│   └── test_orchestrator.py       # v0.6
│
└── data/                          # Created at runtime, gitignored
    ├── conversations.db           # SQLite database
    ├── chroma/                    # ChromaDB storage
    └── wire.jsonl                 # Wiretap log
```

---

## Core Components

### FastAPI Proxy (`proxy.py`)

Implements OpenAI-compatible endpoints. Handles streaming transparently — buffers the full response for logging/embedding while streaming chunks back to the frontend in real time. Integrates the full four-tier hybrid router, multi-backend forwarding, cost tracking, and flight recorder instrumentation.

### Multi-Backend Router (`backends/`)

Priority-based routing across Ollama (local) and OpenRouter (API) with automatic fallback on timeout or error. Each backend implements a common interface (`BaseBackend`) with `forward`, `forward_stream`, `health_check`, and `list_models`. The router aggregates models from all backends for `/v1/models`.

### Dual Storage (`storage/`)

SQLite for structured queries and export (now with `cost_usd` column and schema migrations). ChromaDB for semantic search and RAG. Embeddings are generated asynchronously after the response streams back, adding zero latency to the conversation.

### Tool Registry (`tools/`)

Modular tool system. Each tool is a self-contained module registered at startup. Tools can be invoked by the decision LLM, forced via z-commands, or triggered by hooks.

Built-in tools: web search (DuckDuckGo), web scraper, calculator, datetime, system info, conversation memory, notifier.

### Agents (`agents/`)

The routing brain. Four independent classifiers that work together: session cache, z-command parser, agentic scorer, embedding classifier, and decision LLM.

### Operator Agent (`agents/operator.py`)

LangChain ReAct agent for interactive data and system queries. Five tools plus the optional parallel orchestrator.

### Observability (`flight_recorder.py`, `replay.py`, `semantic_map.py`)

Three independent tools for understanding proxy behavior. Flight recorder captures per-request timelines in an in-memory ring buffer. Conversation replay reconstructs routing context by correlating SQLite messages with wiretap log entries. Semantic map clusters conversation topics using existing ChromaDB embeddings.

### Hooks (`hooks/`)

Pre/post processing pipeline. Drop Python scripts in the hooks directory to extend the proxy's behavior.

### Wiretap (`wiretap.py`)

Structured JSONL log of every message that crosses the wire — including internal routing decisions, tool injections, backend selection, and cost tracking.

### TUI (`tui/`)

Textual-based terminal UI with lavender palette. Launch with `beigebox jack`. Screens: live wire tap feed, config viewer.

---

## API Endpoints

### Core (OpenAI-compatible)
| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completion (streaming + non-streaming) |
| `/v1/models` | GET | List models (aggregated from all backends) |

### BeigeBox
| Endpoint | Method | Description |
|---|---|---|
| `/beigebox/health` | GET | Health check with version |
| `/beigebox/stats` | GET | Quick conversation stats |
| `/beigebox/search` | GET | Semantic search |
| `/api/v1/info` | GET | Version and backend info |
| `/api/v1/config` | GET | Current configuration |
| `/api/v1/status` | GET | Full subsystem status |
| `/api/v1/stats` | GET | Detailed usage stats |
| `/api/v1/tools` | GET | Available tools |
| `/api/v1/operator` | POST | Run the Operator agent |

### v0.6 Endpoints
| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/costs` | GET | Cost tracking (query: `?days=30`) |
| `/api/v1/backends` | GET | Backend health status |
| `/api/v1/flight-recorder` | GET | Recent flight records |
| `/api/v1/flight-recorder/{id}` | GET | Detailed timeline for a request |
| `/api/v1/conversation/{id}/replay` | GET | Conversation replay with routing context |
| `/api/v1/conversation/{id}/semantic-map` | GET | Topic cluster map |
| `/api/v1/orchestrator` | POST | Run parallel LLM tasks |

---

## CLI Commands

Every command has a phreaker name and standard aliases.

```
PHREAKER        STANDARD              WHAT IT DOES
--------        --------              ----------------------------------
dial            start, serve, up      Start the BeigeBox proxy server
jack            tui, ui               Launch the TUI interface
tap             log, tail, watch      Live wiretap — watch the wire
ring            status, ping          Ping a running instance
sweep           search, find          Semantic search over conversations
dump            export                Export conversations to JSON
flash           info, config          Show stats and config at a glance
tone            banner                Print the BeigeBox banner
build-centroids centroids             Generate embedding classifier centroids
operator        op                    Launch the Operator agent (REPL or one-shot)
setup           init                  Interactive first-time setup
```

### Quick Examples

```bash
# Start the proxy
beigebox dial

# Launch the TUI
beigebox jack

# Watch conversations flow in real-time
beigebox tap

# Launch the Operator agent
beigebox operator
beigebox op "what did we discuss about authentication last week?"

# Semantic search across all stored conversations
beigebox sweep "docker networking"

# Export conversations to portable JSON
beigebox dump --output backup.json --pretty

# Build embedding classifier centroids (one-time)
beigebox build-centroids

# Show stats
beigebox flash
```

---

## Configuration

All configuration lives in `config.yaml`. No hardcoded values in the codebase. `runtime_config.yaml` is hot-reloaded on every request — change values there without restarting.

### v0.6 Feature Flags

All v0.6 features are disabled by default. Enable them individually:

```yaml
# Multi-backend routing
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

# Cost tracking
cost_tracking:
  enabled: false

# Orchestrator (parallel LLM tasks)
orchestrator:
  enabled: false
  max_parallel_tasks: 5

# Flight recorder (request timelines)
flight_recorder:
  enabled: false
  max_records: 1000
  retention_hours: 24

# Conversation replay
conversation_replay:
  enabled: false

# Semantic map (topic clustering)
semantic_map:
  enabled: false
  similarity_threshold: 0.5
  max_topics: 50
```

See `config.yaml` for the full configuration reference including backend, routing, tools, decision LLM, operator, hooks, and wiretap settings.

---

## Setup and Installation

### Prerequisites

1. **Ollama** (or any OpenAI-compatible backend):
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull nomic-embed-text          # Required for embeddings
   ollama pull <your-preferred-model>    # At least one chat model
   ```

2. **A frontend** (optional — you can also curl the API directly):
   ```bash
   docker run -d -p 3000:8080 \
     --add-host=host.docker.internal:host-gateway \
     -v open-webui:/app/backend/data \
     --name open-webui \
     ghcr.io/open-webui/open-webui:main
   ```

### Install

```bash
git clone https://github.com/RALaBarge/beigebox.git
cd beigebox

pip install -e .
```

Or with a virtualenv:

```bash
python -m venv venv
source venv/bin/activate
pip install -e .
```

Or use the interactive setup: `./setup.sh`

### Configure

Edit `config.yaml` to point at your backend and specify your models. At minimum set `backend.url` and `backend.default_model`.

### Run

```bash
beigebox dial
```

BeigeBox is now listening on port 8000. Point your frontend at `http://localhost:8000/v1` as an OpenAI-compatible connection (any non-empty string for the API key).

### Optional: Enable Features

```bash
# Build embedding classifier centroids (one-time, requires Ollama + nomic-embed-text)
beigebox build-centroids

# Enable multi-backend: set backends_enabled: true in config.yaml
# Enable cost tracking: set cost_tracking.enabled: true
# Enable flight recorder: set flight_recorder.enabled: true
# Enable decision LLM: pull a small router model, set decision_llm.enabled: true
```

---

## Docker Quickstart

```bash
cd docker
docker compose up -d
```

The compose file brings up BeigeBox + Ollama + Open WebUI with health checks and auto-restart. See `docker/docker-compose.yaml` for details.

---

## Usage

### Watching the Wire

```bash
beigebox tap                        # Live follow (like tail -f for LLM conversations)
beigebox tail --no-follow -n 50     # Last 50 entries, then exit
beigebox log --role user --raw      # Raw JSONL, pipe to jq
```

### Searching Conversations

```bash
beigebox sweep "docker networking"
beigebox search "python async" -n 10
```

### Using Z-Commands

In your chat frontend, prefix any message:

```
z: code Write a binary tree in Rust
z: complex,search What's happening in AI research?
z: calc 2**16 + 3**10
z: help
```

### Checking Costs (v0.6)

```bash
curl http://localhost:8000/api/v1/costs?days=7
```

### Reviewing Request Timelines (v0.6)

```bash
curl http://localhost:8000/api/v1/flight-recorder
curl http://localhost:8000/api/v1/flight-recorder/<record-id>
```

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run v0.6 tests only
pytest tests/test_backends.py tests/test_costs.py tests/test_flight_recorder.py \
       tests/test_replay.py tests/test_semantic_map.py tests/test_orchestrator.py -v

# Quick smoke test (no external dependencies needed)
pytest tests/test_flight_recorder.py tests/test_costs.py tests/test_replay.py -v
```

---

## Roadmap

### Done
- [x] FastAPI proxy with OpenAI-compatible endpoints
- [x] Transparent streaming request/response forwarding
- [x] SQLite conversation logging + ChromaDB embeddings
- [x] Config-driven architecture (no hardcoded model names)
- [x] Tool registry with built-in tools (search, scraper, calc, datetime, sysinfo, memory, notifier)
- [x] Decision LLM for N-way routing and tool detection
- [x] Multi-class embedding classifier (simple / complex / code / creative)
- [x] Z-command user overrides with chaining
- [x] Four-tier hybrid routing with graceful degradation
- [x] Session-aware routing (sticky model within a conversation)
- [x] Agentic intent scorer (pre-filter before embedding classifier)
- [x] Web search augmentation (wired end-to-end)
- [x] RAG context injection (wired end-to-end)
- [x] Decision LLM tool dispatch (all three outputs act)
- [x] Runtime config hot-reload (no restart needed)
- [x] Hooks plugin system
- [x] Token tracking
- [x] Synthetic request filtering
- [x] Docker quickstart with health checks
- [x] Wiretap logging with CLI viewer
- [x] Conversation export and Open WebUI migration scripts
- [x] TUI interface (lavender palette, live tap + config screens)
- [x] Operator agent (LangChain ReAct, web + data + shell)

### v0.6 (Done)
- [x] Multi-backend router (priority-based fallback: Ollama → OpenRouter)
- [x] Cost tracking for API backends (per-request, per-model, per-day, per-conversation)
- [x] Flight recorder (request lifecycle timelines with per-stage timing)
- [x] Conversation replay (full routing context reconstruction)
- [x] Semantic conversation map (topic clustering via embeddings)
- [x] Parallel orchestrator (concurrent LLM task spawning for Operator)
- [x] SQLite schema migrations (backward-compatible column additions)
- [x] 50+ new tests across 6 test modules

### Next
- [ ] Busybox in Dockerfile for predictable operator shell surface
- [ ] Streaming cost tracking (OpenRouter generation ID lookup)
- [ ] `beigebox flash` CLI command for cost summary display
- [ ] Flight recorder TUI screen

### Future
- [ ] Conversation summarization for context window management
- [ ] Web dashboard for browsing stored conversations
- [ ] Multi-model voting / ensemble responses
- [ ] Voice pipeline integration
- [ ] Fine-tuning data export
- [ ] Agent framework with sandboxed execution

---

## Contributing

By contributing to this project, you agree that your contributions will be licensed under the project's AGPLv3 license, and you grant the maintainer(s) a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license to incorporate your contributions into the project and sub-license them under alternative commercial terms.

--

## License

Note for Enterprises: This project is licensed under AGPLv3 to keep it free for the community. If you are a high-revenue carbon-based lifeform, synthetic or otherwise, and require a non-copyleft license (or just want to support the dev), please contact me!

This means if you are gonna make money off of it, you gotta hollar at me first and we can work something out.  I've got 4 dogs and 2 kids and christ they eat a lot...

Otherwise, it is free to use for everyone that is not making a buck from it! If you think of something cool, or have code that matches my style here, please make a PR or an Issue to chat!

---

*BeigeBox — because the most interesting box on the network is the one nobody knows is there.*
