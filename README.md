# BeigeBox - Local LLM Conversation Proxy

A transparent proxy that sits between your LLM frontend (Open WebUI) and your LLM backend (Ollama/llama.cpp), intercepting and storing every conversation while providing extensible tooling via LangChain.

```
+---------------+         +--------------------------------------+         +-----------------+
|               |  HTTP   |          BEIGEBOX               |  HTTP   |                 |
|  Open WebUI   |-------->|                                      |-------->|  Ollama /        |
|  (Frontend)   |<--------|  FastAPI Proxy                       |<--------|  llama.cpp       |
|               |         |  +----------------------------------+|         |  (Backend)       |
|  Port 3000    |         |  | Intercept Layer                  ||         |  Port 11434      |
+---------------+         |  |  - Log user message              ||         +-----------------+
                          |  |  - Log assistant response        ||
                          |  |  - Embed both -> ChromaDB        ||
                          |  |  - (Future) Decision LLM         ||
                          |  +----------------------------------+|
                          |                                      |
                          |  +----------+  +-------------------+ |
                          |  | SQLite   |  | ChromaDB          | |
                          |  | (raw     |  | (vector           | |
                          |  |  convos) |  |  embeddings)      | |
                          |  +----------+  +-------------------+ |
                          |                                      |
                          |  +----------------------------------+|
                          |  | LangChain Tools                  ||
                          |  |  - DuckDuckGo Search             ||
                          |  |  - Web Scraper (BeautifulSoup)   ||
                          |  |  - Google Search (mock/future)   ||
                          |  |  - (Extensible tool registry)    ||
                          |  +----------------------------------+|
                          |                          Port 8000   |
                          +--------------------------------------+
```

---

## Table of Contents

1. [Why This Exists - Design Decisions](#why-this-exists---design-decisions)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Core Components](#core-components)
5. [Configuration](#configuration)
6. [Setup and Installation](#setup-and-installation)
7. [Usage](#usage)
8. [Roadmap](#roadmap)
9. [Hardware Context](#hardware-context)

---

## Why This Exists - Design Decisions

This section documents the reasoning behind every major design choice, captured over multiple brainstorming sessions. These are the decisions we came to and WHY, so future-us does not have to re-derive them.

### The Core Problem

Open WebUI stores conversations in its own SQLite database in its own format. That data is locked inside their schema. If we switch frontends, that history is gone. If we want to search across conversations semantically, we cannot. If we want to augment requests with web data or tool output before they hit the LLM, there is no hook for that.

We need a layer we control that owns the conversation data in a portable, standard format and can extend what the LLM sees and does.

### Decision: Build a Proxy, Not a Plugin

We considered three approaches:

- **Open WebUI plugin**: Tied to their plugin API, breaks when they update, cannot use it with other frontends.
- **OpenRouter / LiteLLM**: Third-party routing. Great for multi-provider access, but we do not need to pay for routing to local models. Does not solve the conversation ownership problem. OpenRouter takes a 5% + $0.35 fee on credit purchases and their ToS allows licensing user content for commercial purposes when logging is opted into.
- **Custom proxy middleware**: Sits between any frontend and any backend. Frontend-agnostic, backend-agnostic. We own every byte.

**We chose the proxy** because it gives us the most control and future flexibility. Open WebUI thinks it is talking to Ollama. Ollama thinks it is getting requests from Open WebUI. Our middleware transparently intercepts everything.

### Decision: OpenAI-Compatible API as the Lingua Franca

Ollama already exposes an OpenAI-compatible API at `/v1/chat/completions`. Open WebUI can connect to any OpenAI-compatible endpoint. Almost every LLM tool on the planet speaks this format.

By implementing the OpenAI-compatible API in our middleware, we get automatic compatibility with virtually any frontend or backend that exists now or will exist in the future. No custom protocols, no vendor lock-in.

This was a core requirement from the start: **generic interfaces, portable everywhere**.

### Decision: Dual Storage - SQLite + ChromaDB

We debated: do we need both SQL and vector storage?

- **SQLite** gives us the raw conversation record. Every message, every timestamp, every model used. It is a single portable file we can copy, query with standard SQL, export to JSON, or migrate to Postgres later. This is our source of truth. Open WebUI itself uses SQLite internally, so we already know the format works and is portable.
- **ChromaDB** gives us semantic search over those conversations. "What did I discuss about Docker networking last month?" becomes a vector similarity query instead of a keyword grep. ChromaDB is also a file-based database - no external server needed. Open WebUI already uses ChromaDB internally for its RAG pipeline, so this is a proven pairing.

**We chose both** because they serve fundamentally different purposes. SQLite is for structured queries and export. ChromaDB is for semantic retrieval and future RAG pipelines. The overhead of maintaining both is minimal since we are writing to them at the same time anyway.

### Decision: nomic-embed-text for Embeddings

Requirements: open source, runs locally via Ollama, likely to still be the standard in 5 years.

- **nomic-embed-text**: Apache 2.0, open weights, open training data, 768 dimensions, 8192 token context. Already runs natively in Ollama. Strong benchmark performance competitive with OpenAI text-embedding-3-small. Nomic is deeply invested in the open embedding ecosystem.
- **mxbai-embed-large**: Good alternative, also in Ollama, but less ecosystem momentum.
- **OpenAI text-embedding-3-small**: Proprietary, requires API key and internet, costs money. Violates our local-first principle.

**We chose nomic-embed-text** for the combination of open license, local execution, benchmark quality, and ecosystem trajectory.

### Decision: LangChain for Tooling (Not Everything)

Early on, we discussed whether to go raw Python or adopt a framework. The conclusion:

- **LangChain** handles the tool ecosystem well - DuckDuckGo, web scraping, Google search, document loaders. Re-implementing all of that from scratch is pointless.
- **LangChain does NOT become the core architecture**. The proxy, the storage, the routing - that is all our code, plain Python and FastAPI. LangChain is a tool provider that we call when we need tools.

This means if LangChain falls out of favor or a better tool framework emerges, we swap out one module. The middleware itself does not care.

We originally wanted to "understand the underlying primitives before adopting frameworks like LangChain" - and we did. We built the agent loop from scratch, we understand message passing, context windows, and tool calling at the raw API level. We built a working autonomous agent (agent.py) that executes shell commands in LXC containers using raw Ollama tool calls. Now we are adopting LangChain specifically for its tool integrations, not as a crutch.

### Decision: Config-Driven, Model as a Variable

From the start, the requirement was: "make the model a variable I can modify by changing a config file." No hardcoded model names anywhere in the codebase. The middleware reads `config.yaml` at startup and uses whatever is specified there.

This also applies to the embedding model, the backend URL, tool toggles, and storage paths. If it might change, it is in the config.

### Decision: Default to Capturing Everything

The middleware captures both sides of every conversation by default - user messages and assistant responses. No opt-in, no toggle to remember. The storage is local, the data is ours. We can always filter or delete later. We cannot recover conversations we did not capture.

### Decision: Future Decision LLM in the Middleware

The long-term vision is to have a small, fast LLM running inside the middleware itself that makes routing and augmentation decisions. For example:

- User asks a question -> decision LLM determines "this needs web search" -> LangChain fetches data -> augmented prompt goes to the main LLM
- User asks a coding question -> decision LLM routes to the coder model (e.g., qwen2.5-coder:14b)
- User asks a general question -> decision LLM routes to the general model (e.g., qwen3:32b)

We are NOT building this yet. The architecture supports it - the intercept layer is where this logic will live. For now, LangChain tools are invoked based on configuration, not LLM decisions. The Qwen3-30B-A3B MoE model (only 3B active parameters per token) is a strong candidate for this role when we get there - fast enough for inline decisions without competing for GPU resources with the main model.

---

## Architecture

### Data Flow - Chat Request

```
1. User types message in Open WebUI
2. Open WebUI sends POST /v1/chat/completions to middleware (port 8000)
3. Middleware intercepts:
   a. Logs user message to SQLite
   b. Embeds user message via nomic-embed-text -> ChromaDB
   c. (If enabled) Runs LangChain tools to augment the request
   d. Forwards (possibly augmented) request to Ollama (port 11434)
4. Ollama returns response (streamed)
5. Middleware intercepts response:
   a. Logs assistant message to SQLite
   b. Embeds assistant message -> ChromaDB
   c. Streams response back to Open WebUI
6. User sees response in Open WebUI (no idea middleware exists)
```

### Data Flow - Streaming

Ollama streams responses by default (Server-Sent Events). The middleware must handle streaming transparently - it buffers the full response for logging/embedding while simultaneously streaming chunks back to the frontend so the user does not experience added latency.

### Why Not Just Use Open WebUI's Built-in RAG?

Open WebUI has RAG built in (document upload, embedding, vector search). But it is their implementation, their schema, their pipeline. If we move to a different frontend, or want to search conversations from a CLI tool, or want to feed conversation context into an agent running outside the browser, Open WebUI's internal ChromaDB is not accessible. Our middleware stores everything independently and exposes it however we want.

---

## Project Structure

```
beigebox/
|-- README.md                  # This file - the plan, the why, the how
|-- config.yaml                # All user-configurable settings
|-- requirements.txt           # Python dependencies
|-- .env.example               # Environment variables template
|-- .gitignore                 # Standard Python + data exclusions
|
|-- beigebox/
|   |-- __init__.py
|   |-- main.py                # FastAPI app - the proxy entry point
|   |-- proxy.py               # Request/response interception and forwarding
|   |-- config.py              # Config loader (reads config.yaml)
|   |
|   |-- storage/
|   |   |-- __init__.py
|   |   |-- sqlite_store.py    # Raw conversation storage (SQLite)
|   |   |-- vector_store.py    # Embedding storage (ChromaDB + nomic)
|   |   |-- models.py          # Data models / schemas
|   |
|   |-- tools/
|   |   |-- __init__.py
|   |   |-- registry.py        # Tool registration and dispatch
|   |   |-- web_search.py      # DuckDuckGo search via LangChain
|   |   |-- web_scraper.py     # URL content extraction (BeautifulSoup)
|   |   |-- google_search.py   # Google search (mock now, API key later)
|   |
|   |-- agents/
|       |-- __init__.py
|       |-- decision.py        # (Future) Decision LLM for routing/augmentation
|
|-- data/                      # Created at runtime, gitignored
|   |-- conversations.db       # SQLite database
|   |-- chroma/                # ChromaDB persistent storage
|
|-- scripts/
|   |-- export_conversations.py    # Export SQLite -> JSON (OpenAI format)
|   |-- search_conversations.py    # CLI semantic search over conversations
|   |-- migrate_open_webui.py      # Import Open WebUI history into middleware DB
|
|-- tests/
|   |-- test_proxy.py
|   |-- test_storage.py
|   |-- test_tools.py
|
|-- docker/
    |-- Dockerfile             # Middleware container
    |-- docker-compose.yaml    # Full stack: middleware + ollama + open-webui
```

---

## Core Components

### 1. FastAPI Proxy (beigebox/main.py, beigebox/proxy.py)

Implements the OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints. Acts as a transparent pass-through with interception hooks. Handles both streaming and non-streaming responses.

Open WebUI connects to `http://localhost:8000/v1` as its "OpenAI-compatible" backend. The middleware forwards to Ollama at `http://localhost:11434/v1`.

We chose FastAPI over Flask because it has native async support (critical for streaming proxy behavior) and built-in OpenAPI docs for free.

### 2. SQLite Storage (beigebox/storage/sqlite_store.py)

Stores every conversation turn with: message ID, conversation ID, role (user/assistant/system), content, model used, timestamp, and token counts. Schema is intentionally simple and portable - designed for easy export to JSON in OpenAI message format.

The export format matches the OpenAI messages array structure: `[{role, content, timestamp, model}]`. This is the most portable format in the LLM ecosystem - import it into any tool that speaks OpenAI API.

### 3. Vector Storage (beigebox/storage/vector_store.py)

Every message gets embedded via Ollama's `/api/embeddings` endpoint using `nomic-embed-text` and stored in ChromaDB with metadata linking back to the SQLite record. Enables semantic search across all conversation history.

Embeddings are generated asynchronously after the response is streamed back to the user, so they never add latency to the conversation.

### 4. LangChain Tools (beigebox/tools/)

Tool integrations for extending what the LLM can do:

- **DuckDuckGo Search**: Free, no API key. Default web search tool. This was chosen as the primary search because it requires zero setup.
- **Web Scraper**: Given a URL, fetches and extracts clean text content. Uses BeautifulSoup (requests + bs4).
- **Google Search**: Stubbed out with a mock function. Swap in a real API key when ready. The mock returns realistic-looking results so the rest of the pipeline can be tested without an API key.
- **Tool Registry**: Central registry so new tools can be added by dropping a file in the tools directory and adding it to config.yaml.

Tools are invoked by the middleware before forwarding the request to Ollama. The tool output gets injected into the system prompt or appended to the message context.

### 5. Decision LLM (Future - beigebox/agents/decision.py)

Placeholder module for the future routing/augmentation LLM. The plan is to run a small, fast model (e.g., Qwen 3B MoE or similar) that reads the user input and decides: does this need web search? Should it route to the coder model or general model? Should it pull from conversation history via RAG?

This is the "brain" of the middleware - but we build the body first.

---

## Configuration

All configuration lives in `config.yaml` at the project root. No hardcoded values in the codebase.

```yaml
# config.yaml

# --- Backend ---
backend:
  url: "http://localhost:11434"    # Ollama base URL
  default_model: "qwen3:32b"       # Default model if none specified
  timeout: 120                      # Request timeout in seconds

# --- Middleware Server ---
server:
  host: "0.0.0.0"
  port: 8000

# --- Embedding ---
embedding:
  model: "nomic-embed-text"         # Embedding model (must be pulled in Ollama)
  backend_url: "http://localhost:11434"  # Can be separate from chat backend

# --- Storage ---
storage:
  sqlite_path: "./data/conversations.db"
  chroma_path: "./data/chroma"
  log_conversations: true           # Master switch for conversation capture

# --- Tools ---
tools:
  enabled: true                     # Master switch for tool augmentation
  web_search:
    enabled: true
    provider: "duckduckgo"          # "duckduckgo" or "google"
    max_results: 5
  web_scraper:
    enabled: true
    max_content_length: 10000       # Chars to extract per page
  google_search:
    enabled: false
    api_key: ""                     # Set in .env, referenced here
    cse_id: ""

# --- Decision LLM (Future) ---
decision_llm:
  enabled: false
  model: "qwen3:3b"                # Small fast model for routing decisions
  backend_url: "http://localhost:11434"

# --- Logging ---
logging:
  level: "INFO"                     # DEBUG, INFO, WARNING, ERROR
  file: "./data/middleware.log"
```

---

## Setup and Installation

### Prerequisites

- Python 3.11+
- Ollama running with at least one chat model pulled
- nomic-embed-text pulled in Ollama: `ollama pull nomic-embed-text`
- Open WebUI (or any OpenAI-compatible frontend)

### Install

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/beigebox.git
cd beigebox

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Pull the embedding model
ollama pull nomic-embed-text
```

### Configure

Edit `config.yaml` to match your setup. At minimum, verify:

- `backend.url` points to your Ollama instance
- `backend.default_model` matches a model you have pulled
- `storage` paths are writable

### Run

```bash
# Start the middleware
python -m beigebox.main

# Middleware is now listening on port 8000
```

### Connect Open WebUI

In Open WebUI settings, add an OpenAI-compatible connection:

- **API Base URL**: `http://localhost:8000/v1`
- **API Key**: `not-needed` (any non-empty string)

Open WebUI will now route all requests through the middleware.

---

## Usage

### Searching Conversations

```bash
# Semantic search across all stored conversations
python scripts/search_conversations.py "docker networking issue"

# Export all conversations to portable JSON
python scripts/export_conversations.py --output my_conversations.json

# Import existing Open WebUI history
python scripts/migrate_open_webui.py --source ~/.config/open-webui/webui.db
```

### Adding New Tools

1. Create a new file in `beigebox/tools/` (e.g., `arxiv_search.py`)
2. Implement the tool following the pattern in existing tools
3. Register it in `beigebox/tools/registry.py`
4. Add configuration for it in `config.yaml`
5. Restart the middleware

---

## Roadmap

### Phase 1: Foundation (Current)
- [ ] FastAPI proxy with OpenAI-compatible endpoints
- [ ] Transparent request/response forwarding with streaming
- [ ] SQLite conversation logging
- [ ] ChromaDB embedding storage with nomic-embed-text
- [ ] Basic config.yaml loader
- [ ] LangChain DuckDuckGo search integration
- [ ] LangChain web scraper integration
- [ ] Google search mock/stub
- [ ] Conversation export script (SQLite to JSON)

### Phase 2: Intelligence
- [ ] Decision LLM for tool invocation ("does this need a web search?")
- [ ] RAG over conversation history (inject relevant past context)
- [ ] Model cascading (try local model, escalate to bigger/paid if needed)
- [ ] Smart routing (coder model for code questions, general for general)

### Phase 3: Advanced Features
- [ ] Conversation summarization for context window management
- [ ] Multi-model voting / ensemble responses
- [ ] Open WebUI history migration script
- [ ] Web dashboard for searching/browsing stored conversations
- [ ] Conversation memory extraction (extract facts, similar to how Claude builds userMemories)

### Phase 4: Ecosystem
- [ ] Docker Compose for full stack deployment
- [ ] Multiple simultaneous frontends (Open WebUI + CLI + API clients)
- [ ] Voice pipeline integration (Whisper STT -> middleware -> TTS)
- [ ] Fine-tuning data export (conversations to training format)
- [ ] Agent framework with shell/code execution in sandboxed LXC containers

---

## Hardware Context

This project is built for and tested on:

- **CPU**: AMD 5800X3D
- **RAM**: 128 GB system memory
- **GPU**: NVIDIA RTX 4070 Ti Super (16 GB VRAM)
- **OS**: Pop!_OS (Ubuntu-based)
- **Backend**: Ollama with Qwen models (32B Q4 with GPU offload, 14B full GPU)

The middleware itself has negligible resource requirements. Embedding with nomic-embed-text is fast and lightweight. All the heavy lifting happens in Ollama.

For the 32B model: Ollama handles the CPU/GPU split gracefully. The model partially loads into 16GB VRAM and spills the rest into system RAM. For a 14B model, it fits entirely on GPU for fast inference. This was a key factor in choosing Ollama over vLLM (which cannot spill to system RAM) or raw llama.cpp (which requires manual layer configuration).

---

## Key References

- Ollama API docs: https://github.com/ollama/ollama/blob/main/docs/api.md
- OpenAI API spec (what we implement): https://platform.openai.com/docs/api-reference/chat
- ChromaDB docs: https://docs.trychroma.com/
- Nomic Embed: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
- LangChain tools: https://python.langchain.com/docs/integrations/tools/
- Open WebUI docs: https://docs.openwebui.com/

---

*This README serves as the project plan and architectural record. Update it as decisions evolve.*
