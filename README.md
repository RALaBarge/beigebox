# BeigeBox

**Tap the line. Own the conversation.**

A transparent middleware proxy for local LLM stacks. Sits between your frontend (Open WebUI, etc.) and your backend (Ollama, llama.cpp, etc.), intercepting and storing every conversation while providing intelligent routing, extensible tooling, and user-level overrides.

```
+---------------+         +--------------------------------------+         +-----------------+
|               |  HTTP   |            BEIGEBOX                  |  HTTP   |                 |
|  Open WebUI   |-------->|                                      |-------->|  Ollama /        |
|  (Frontend)   |<--------|  FastAPI Proxy                       |<--------|  llama.cpp       |
|               |         |                                      |         |  (Backend)       |
|  Port 3000    |         |  +------ Hybrid Router -----------+  |         |  Port 11434      |
+---------------+         |  | 0. Session Cache  (instant)    |  |         +-----------------+
                          |  | 1. Z-Commands     (instant)    |  |
                          |  | 2. Agentic Scorer (instant)    |  |
                          |  | 3. Embedding Class (~50ms)     |  |
                          |  | 4. Decision LLM   (~500ms-2s)  |  |
                          |  +---------------------------------+  |
                          |                                      |
                          |  +----------+  +-------------------+ |
                          |  | SQLite   |  | ChromaDB          | |
                          |  | (raw     |  | (vector           | |
                          |  |  convos) |  |  embeddings)      | |
                          |  +----------+  +-------------------+ |
                          |                                      |
                          |  +------ Tool Registry ------------+ |
                          |  | Web Search  | Calculator        | |
                          |  | Web Scraper | DateTime          | |
                          |  | Memory/RAG  | System Info       | |
                          |  | Notifier    | (extensible)      | |
                          |  +--------------------------------- + |
                          |                                      |
                          |  +------ Operator Agent ----------+ |
                          |  | LangChain ReAct agent          | |
                          |  | Web search + scrape            | |
                          |  | Semantic conversation search   | |
                          |  | Named SQLite queries           | |
                          |  | Allowlisted shell              | |
                          |  +---------------------------------+ |
                          |                          Port 8000   |
                          +--------------------------------------+
```

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Architecture](#architecture)
3. [Hybrid Routing](#hybrid-routing)
4. [Z-Commands](#z-commands)
5. [Operator Agent](#operator-agent)
6. [Project Structure](#project-structure)
7. [Core Components](#core-components)
8. [CLI Commands](#cli-commands)
9. [Configuration](#configuration)
10. [Setup and Installation](#setup-and-installation)
11. [Docker Quickstart](#docker-quickstart)
12. [Usage](#usage)
13. [Roadmap](#roadmap)
14. [Docs](#docs)

---

## What It Does

BeigeBox is a proxy that makes your local LLM stack smarter without changing anything about how you use it.

Your frontend thinks it's talking to Ollama. Ollama thinks it's getting requests from your frontend. BeigeBox sits in the middle, transparently intercepting every message to:

- **Store** every conversation in portable SQLite + semantic ChromaDB (you own the data, not the frontend)
- **Route** requests to the right model based on complexity (fast model for simple questions, large model for hard ones, code model for programming, creative model for writing)
- **Augment** requests with tool output before they hit the LLM (web search, conversation memory, math, system info)
- **Override** any decision with user-level z-commands when you know better than the router
- **Persist** routing decisions within a conversation so the model doesn't switch mid-thread

Everything degrades gracefully. If routing is disabled, BeigeBox is a transparent proxy. If tools are disabled, requests pass through unaugmented. If storage fails, the conversation still works. Each feature is independent and optional.

---

## Architecture

### Data Flow

```
1. User types message in Open WebUI
2. Open WebUI sends POST /v1/chat/completions to BeigeBox (port 8000)
3. BeigeBox intercepts:
   a. Check session cache — same conversation? Use the same model. (instant)
   b. Check for z-command override (instant)
   c. Run agentic scorer — flag tool-intent prompts (instant)
   d. Run embedding classifier (fast path, ~50ms)
   e. If borderline, escalate to decision LLM (slow path, ~500ms-2s)
   f. Execute any forced/decided tools (web search, RAG, etc.)
   g. Log user message to SQLite + embed to ChromaDB
   h. Forward (possibly augmented, possibly rerouted) request to Ollama
4. Ollama returns response (streamed)
5. BeigeBox intercepts response:
   a. Log assistant message to SQLite + embed to ChromaDB
   b. Track token usage
   c. Stream response back to Open WebUI
6. User sees response (no idea BeigeBox exists)
```

### OpenAI-Compatible API

BeigeBox implements the OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`). This means it works with any frontend or backend that speaks this format — which is virtually everything in the LLM ecosystem. No custom protocols, no vendor lock-in.

### Dual Storage

- **SQLite**: Source of truth. Every message, timestamp, model, token count. Portable single file you can query with standard SQL or export to JSON.
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

Uses the same `nomic-embed-text` model already loaded for ChromaDB — zero new dependencies, zero new models to pull. The classifier picks the best-matching route and computes a confidence gap between the top two scores. Clear wins skip Tier 4; borderline cases escalate.

Run `beigebox build-centroids` once to generate the centroid vectors from built-in seed prompts.

### Tier 4: Decision LLM (~500ms–2s)

A small, fast model reads the prompt and outputs structured JSON: which route to use, whether tools are needed, confidence level. Only called for borderline cases where the embedding classifier isn't confident enough.

Requires pulling a small model and setting `decision_llm.enabled: true` in config. The decision LLM can also trigger tool augmentation — web search, RAG context, or arbitrary registered tools — which are injected into the request before forwarding.

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

Aliases: `websearch` → search, `rag`/`recall` → memory, `math` → calc, `date`/`clock` → time, `system`/`status` → sysinfo.

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

```bash
# Interactive REPL
beigebox operator

# One-shot query
beigebox op "summarise everything we discussed about docker last week"
```

The shell tool only runs binaries explicitly listed in `config.yaml` under `operator.shell_allowlist`. Dangerous patterns (pipes to shell, command substitution, etc.) are blocked regardless of allowlist.

---

## Project Structure

```
beigebox/
├── README.md
├── LICENSE                        # MIT
├── config.yaml                    # All runtime configuration
├── runtime_config.yaml            # Hot-reloaded overrides (no restart needed)
├── pyproject.toml                 # Package metadata + CLI entry point
├── requirements.txt
├── setup.sh                       # Interactive setup script
│
├── beigebox/
│   ├── __init__.py
│   ├── __main__.py                # python -m beigebox entry
│   ├── cli.py                     # CLI commands
│   ├── main.py                    # FastAPI app initialization
│   ├── proxy.py                   # Request interception, hybrid routing, forwarding
│   ├── config.py                  # Config loader with hot-reload support
│   ├── wiretap.py                 # Structured JSONL wire log
│   │
│   ├── agents/
│   │   ├── decision.py            # Decision LLM (Tier 4 router)
│   │   ├── embedding_classifier.py # Embedding classifier (Tier 3 router)
│   │   ├── zcommand.py            # Z-command parser (Tier 1 overrides)
│   │   ├── agentic_scorer.py      # Agentic intent scorer (Tier 2 pre-filter)
│   │   ├── operator.py            # LangChain ReAct operator agent
│   │   └── centroids/             # Pre-computed centroid .npy files
│   │
│   ├── storage/
│   │   ├── sqlite_store.py        # Raw conversation storage
│   │   ├── vector_store.py        # ChromaDB embedding storage
│   │   └── models.py              # Data models / schemas
│   │
│   ├── tools/
│   │   ├── registry.py            # Tool registration and dispatch
│   │   ├── web_search.py          # DuckDuckGo search (LangChain)
│   │   ├── web_scraper.py         # URL content extraction
│   │   ├── google_search.py       # Google search (stub, API key optional)
│   │   ├── calculator.py          # Safe math expression evaluator
│   │   ├── datetime_tool.py       # Time, date, timezone tools
│   │   ├── system_info.py         # Host system stats
│   │   ├── memory.py              # Semantic search over past conversations
│   │   └── notifier.py            # Webhook/notification dispatch
│   │
│   └── tui/
│       ├── __init__.py
│       ├── app.py                 # BeigeBoxApp, SCREEN_REGISTRY
│       ├── styles/
│       │   └── main.tcss          # All styling, lavender palette
│       └── screens/
│           ├── __init__.py
│           ├── base.py            # BeigeBoxPane base class
│           ├── config.py          # Config panel
│           └── tap.py             # Live wire feed panel
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
├── docs/
│   └── 2600/                      # Design notes, research, theorycrafting
│
├── tests/
│   ├── test_proxy.py
│   ├── test_storage.py
│   ├── test_tools.py
│   ├── test_decision.py
│   ├── test_hooks.py
│   ├── test_new_tools.py
│   └── test_zcommand.py
│
└── data/                          # Created at runtime, gitignored
    ├── conversations.db           # SQLite database
    ├── chroma/                    # ChromaDB storage
    └── wire.jsonl                 # Wiretap log
```

---

## Core Components

### FastAPI Proxy (`proxy.py`)

Implements OpenAI-compatible endpoints. Handles streaming transparently — buffers the full response for logging/embedding while streaming chunks back to the frontend in real time. Integrates the full four-tier hybrid router including session cache, agentic scorer, embedding classifier, and decision LLM. All three decision LLM outputs (`tools`, `needs_search`, `needs_rag`) are fully wired and act on every request.

### Dual Storage (`storage/`)

SQLite for structured queries and export. ChromaDB for semantic search and RAG. Embeddings are generated asynchronously after the response streams back, adding zero latency to the conversation.

### Tool Registry (`tools/`)

Modular tool system. Each tool is a self-contained module registered at startup. Tools can be invoked by the decision LLM, forced via z-commands, or triggered by hooks. New tools are added by dropping a file in the tools directory and registering it.

Built-in tools: web search (DuckDuckGo), web scraper, calculator, datetime, system info, conversation memory, notifier. Google search is stubbed for future API key integration.

### Agents (`agents/`)

The routing brain. Four independent classifiers that work together:
- **Session Cache**: In-memory, TTL-based, stickiness within a conversation
- **Z-Command Parser**: Regex-based, instant, user-controlled
- **Agentic Scorer**: Keyword-based intent detection, flags tool-use prompts
- **Embedding Classifier**: 4-way vector similarity (simple/complex/code/creative)
- **Decision LLM**: Full model inference, handles edge cases, triggers tool augmentation

### Operator Agent (`agents/operator.py`)

LangChain ReAct agent for interactive data and system queries. Accessible via `beigebox operator`. Five tools: web search, web scrape, conversation search, named database queries, allowlisted shell.

### Hooks (`hooks/`)

Pre/post processing pipeline. Drop Python scripts in the hooks directory to extend the proxy's behavior. Built-in hooks include synthetic request filtering and RAG context injection.

### Wiretap (`wiretap.py`)

Structured JSONL log of every message that crosses the wire — including internal routing decisions, tool injections, and session cache hits. The `beigebox tap` command renders a color-coded live view.

### TUI (`tui/`)

Textual-based terminal UI with lavender palette. Launch with `beigebox jack`. Screens: live wire tap feed, config viewer.

### Config Hot-Reload (`config.py`)

`get_runtime_config()` checks the mtime of `runtime_config.yaml` on every call and reloads if changed. Override routing behavior, tool settings, or session TTL without restarting the proxy.

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

# Check if BeigeBox is running
beigebox ring

# Semantic search across all stored conversations
beigebox sweep "docker networking"

# Export conversations to portable JSON
beigebox dump --output backup.json --pretty

# Build embedding classifier centroids (one-time, requires Ollama running)
beigebox build-centroids

# Show stats
beigebox flash
```

---

## Configuration

All configuration lives in `config.yaml`. No hardcoded values in the codebase. `runtime_config.yaml` is hot-reloaded on every request — change values there without restarting.

```yaml
# --- Backend ---
backend:
  url: "http://localhost:11434"
  default_model: "your-model-here"
  timeout: 120

# --- Middleware Server ---
server:
  host: "0.0.0.0"
  port: 8000

# --- Embedding ---
embedding:
  model: "nomic-embed-text"
  backend_url: "http://localhost:11434"

# --- Storage ---
storage:
  sqlite_path: "./data/conversations.db"
  chroma_path: "./data/chroma"
  log_conversations: true

# --- Routing ---
routing:
  session_ttl_seconds: 1800        # Sticky model per conversation (30 min)

# --- Tools ---
tools:
  enabled: true
  web_search:
    enabled: true
    provider: "duckduckgo"
    max_results: 5
  web_scraper:
    enabled: true
  calculator:
    enabled: true
  datetime:
    enabled: true
  system_info:
    enabled: true
  memory:
    enabled: true
    max_results: 3
    min_score: 0.3

# --- Decision LLM ---
decision_llm:
  enabled: false
  model: "your-router-model"
  backend_url: "http://localhost:11434"
  timeout: 5
  max_tokens: 256
  routes:
    default:
      model: "your-default-model"
      description: "General purpose"
    code:
      model: "your-code-model"
      description: "Code generation and debugging"
    creative:
      model: "your-creative-model"
      description: "Writing, brainstorming, creative tasks"
    large:
      model: "your-large-model"
      description: "Complex reasoning and analysis"
    fast:
      model: "your-fast-model"
      description: "Quick responses, simple questions"

# --- Operator Agent ---
operator:
  model: "your-operator-model"
  shell_allowlist:
    - "ls"
    - "cat"
    - "grep"
    - "find"
    - "df"
    - "free"
    - "ps"
    - "git"

# --- Hooks ---
hooks:
  directory: "./hooks"

# --- Wiretap ---
wiretap:
  path: "./data/wire.jsonl"
```

Models are referenced by route name throughout the codebase, never by model string. Change models by editing the routes in config.yaml — no code changes needed.

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

### Optional: Enable Hybrid Routing

```bash
# Build embedding classifier centroids (one-time, requires Ollama + nomic-embed-text running)
beigebox build-centroids

# For Tier 4: pull a small model, then set decision_llm.enabled: true in config.yaml
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

### Exporting Data

```bash
beigebox dump --output backup.json --pretty
```

### Importing Open WebUI History

```bash
python scripts/migrate_open_webui.py --source ~/.config/open-webui/webui.db
```

### Using Z-Commands

In your chat frontend, prefix any message:

```
z: code Write a binary tree in Rust
z: complex,search What's happening in AI research?
z: calc 2**16 + 3**10
z: help
```

### Operator Agent

```bash
beigebox operator
# > what models have I been using most this week?
# > search the web for llama 3.2 benchmarks and summarise
# > show me all conversations where we discussed authentication
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

### Next
- [ ] Cost tracking for paid API backends (token × price per model → SQLite)
- [ ] Busybox in Dockerfile for predictable operator shell surface

### Future
- [ ] Conversation summarization for context window management
- [ ] Web dashboard for browsing stored conversations
- [ ] Multi-model voting / ensemble responses
- [ ] Voice pipeline integration
- [ ] Fine-tuning data export
- [ ] Agent framework with sandboxed execution

---

## Docs

Design notes, research, and theorycrafting live in `docs/2600/`:
- `routing-theory.md` — NadirClaw analysis, embedding classifier design, centroid generation
- `design-decisions.md` — Architectural decisions and alternatives considered

---

## License

MIT. Do whatever you want, don't sue me.

---

*BeigeBox — because the most interesting box on the network is the one nobody knows is there.*
