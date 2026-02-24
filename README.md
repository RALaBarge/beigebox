# BeigeBox

**Tap the line. Control the carrier.**

Transparent middleware proxy for local LLM stacks. Sits between your frontend (Open WebUI, etc.) and your backend (Ollama, etc.) and intercepts every request to add intelligent routing, storage, observability, tooling, and security — without either end knowing it's there.  

Just getting started and not sure what front end to use?  We also offer a Javascript free* HTTP page complete with ai harnessing and orchestration tooling.  Have one AI drive a bunch of AI -- man its a crazy future!

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
                          |  Harness · Orchestrator · Voice      |
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

**Tier 3 — Embedding Classifier (~50ms):** Pre-computed centroid vectors classify prompts into simple, complex, code, or creative via cosine similarity. Centroids are built automatically at startup if they don't exist. Can also be triggered from the Config tab.

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

`beigebox operator` launches a LangChain ReAct agent with web search, web scrape, conversation search, database queries, and an allowlisted shell. In Docker the shell routes through the hardened `bb` busybox wrapper. Also available in the web UI Operator tab (tab 6).

```bash
beigebox operator
beigebox op "what did we discuss about routing last week"
```

The Operator tab also supports routing to a specific model instead of the ReAct agent — useful for direct model queries without the routing pipeline overhead.

---

## Harness — Parallel Agent Runner

The Harness tab (tab 7) runs the same prompt against multiple models or agents simultaneously and shows all outputs side-by-side in a paged 2x2 grid. Two modes:

**Manual mode** — you pick the targets (any loaded model or the Operator agent) and send a prompt. All targets receive it in parallel and stream results independently.

**Orchestrated mode** — you describe a goal and a master LLM takes over. It plans, delegates subtasks to the available target pool, evaluates the collected results, and iterates until satisfied (or the round cap is hit). The master pane shows live reasoning including plans, rationale, per-round evaluation, and the final synthesized answer. Worker panes appear dynamically as tasks are dispatched.

Orchestrated mode streams SSE events:

```
{type:"start"}    -- goal confirmed, model and targets listed
{type:"plan"}     -- round N plan with task breakdown and rationale per target
{type:"dispatch"} -- tasks fired
{type:"result"}   -- individual worker result (one per task)
{type:"evaluate"} -- master assessment: sufficient or needs more
{type:"finish"}   -- synthesized answer, round count
{type:"error"}    -- something went wrong
```

The orchestrator LLM uses temperature 0.2 for deterministic planning. JSON parsing is fault-tolerant — handles markdown-fenced output and partial JSON.

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

## Voice / Push-to-Talk

Disabled by default. Enable in the Config tab (toggle **Voice / PTT**) or set `voice_enabled: true` in `runtime_config.yaml`. Requires an STT service reachable at `/v1/audio/transcriptions` — BeigeBox forwards the request transparently.

Once enabled, a microphone button appears in the chat input bar:

- **Click** to toggle recording on/off
- **Hold** for push-to-talk (releases on mouseup / touchend)
- **Hotkey** — set `voice_hotkey` in runtime config (e.g. `v`) to toggle with a keypress

Audio is captured as webm/opus (falling back to mp4 on Safari), sent to `/v1/audio/transcriptions` as multipart form, and the transcribed text is auto-sent as a chat message.

---

## Web UI

Single-file, no build step, no external JS dependencies. Served at `http://localhost:8000`.

| Tab | Key | Contents |
|---|---|---|
| Dashboard | 1 | Stats cards, subsystem health, backends, cost charts, model performance |
| Chat | 2 | Multi-pane streaming chat, per-pane model/target selector, fan-out to all panes |
| Conversations | 3 | Semantic search grouped by conversation, replay, per-message forking |
| Flight Recorder | 4 | Request timelines with per-stage latency bars |
| Tap | 5 | Wire log, role/direction filters (persisted to localStorage), live mode |
| Operator | 6 | ReAct agent REPL with backend/model target selector |
| Harness | 7 | Parallel agent runner — Manual and Orchestrated modes |
| Config | 8 | Full config viewer/editor, feature flag toggles with inline sub-options, Save & Apply |

**Multi-pane chat** — Add up to 20 panes with the + button. Each pane has its own target (model or @operator). Send broadcasts to all visible panes simultaneously. Navigate pages with [ / ]. Close a pane with x (last pane clears instead of closing).

**Vi mode** — Disabled by default. Zero bytes loaded when off. Toggle via the pi button (bottom-left) or `web_ui_vi_mode: true` in `runtime_config.yaml`.

**Palette** — Set `web_ui_palette` in runtime config or the Config tab: `default`, `phosphor`, `cobalt`, `sakura`, `slate`.

**Forking** — Every message in replay view has a fork button. Branches the conversation from that point into a new ID.

**Config tab** — Shows the full merged config (config.yaml + runtime overrides) in grouped sections. Feature flags expand inline sub-options when toggled. All changes hot-applied without restart. Friendly error messages on disabled endpoints link directly to the Config tab.

**Error handling** — Every disabled feature shows a contextual callout explaining what to enable and where. 404s from disabled features are distinguished from real errors.

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
    orchestrator.py              parallel LLM task spawner (used by Operator tool)

    agents/
      decision.py                Decision LLM -- Tier 4
      embedding_classifier.py    centroid-based classifier -- Tier 3
      agentic_scorer.py          keyword intent pre-filter -- Tier 2
      zcommand.py                z-command parser -- Tier 1
      operator.py                LangChain ReAct agent
      harness_orchestrator.py    goal-directed multi-agent coordinator (Harness tab)

    backends/
      base.py                    BackendResponse dataclass, BaseBackend ABC
      ollama.py                  Ollama backend
      openrouter.py              OpenRouter backend, streaming cost capture
      openai_compat.py           generic OpenAI-compatible backend (llama.cpp, vLLM, TGI, etc.)
      retry_wrapper.py           exponential backoff retry wrapper for any backend
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
      system_info.py             respects operator.shell_binary -- bb in Docker
      memory.py
      notifier.py

    web/
      index.html                 single-file web UI, 8 tabs, no build step
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
    smoke.sh                     full stack validation

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
| `/v1/audio/transcriptions` | POST | STT -- forwarded to configured voice service |
| `/v1/audio/speech` | POST | TTS -- forwarded to configured voice service |
| `/v1/audio/translations` | POST | Audio translation, forwarded |

### Ollama-native (forwarded and logged)

`/api/tags` `/api/chat` `/api/generate` `/api/pull` `/api/embed` `/api/show` `/api/ps` `/api/version` -- all forwarded transparently to the backend.

### BeigeBox

| Endpoint | Method | Description |
|---|---|---|
| `/beigebox/health` | GET | Health check |
| `/api/v1/info` | GET | Version and feature flags |
| `/api/v1/config` | GET / POST | Full config read / save runtime settings (all keys hot-applied) |
| `/api/v1/status` | GET | Subsystem status |
| `/api/v1/stats` | GET | Conversation and token stats |
| `/api/v1/costs` | GET | Cost breakdown, `?days=30` |
| `/api/v1/model-performance` | GET | Latency percentiles by model |
| `/api/v1/tap` | GET | Wire log with filters |
| `/api/v1/search` | GET | Semantic search grouped by conversation |
| `/api/v1/flight-recorder` | GET | Request timelines |
| `/api/v1/flight-recorder/{id}` | GET | Detailed record with event breakdown |
| `/api/v1/conversation/{id}/replay` | GET | Replay with routing context |
| `/api/v1/conversation/{id}/fork` | POST | Fork conversation at message N |
| `/api/v1/conversation/{id}/semantic-map` | GET | Topic cluster map |
| `/api/v1/build-centroids` | POST | Rebuild embedding classifier centroids |
| `/api/v1/operator` | POST | Run Operator agent |
| `/api/v1/orchestrator` | POST | Run parallel task plan |
| `/api/v1/harness/orchestrate` | POST | Goal-directed harness master (SSE stream) |
| `/api/v1/harness/{run_id}` | GET | Retrieve stored harness run by ID |
| `/api/v1/harness` | GET | List recent harness runs |
| `/api/v1/ensemble` | POST | Multi-model ensemble vote -- judge selects best response |
| `/api/v1/backends` | GET | Backend health and status |
| `/{path:path}` | ANY | Catch-all -- forwards unknown paths to backend |

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

`config.yaml` is the main config. `runtime_config.yaml` is hot-reloaded on every request -- no restart needed. Both are fully editable from the web UI Config tab.

The Config tab exposes every section of config.yaml in grouped, labelled fields. Feature flag toggles expand inline sub-options. `GET /api/v1/config` returns the full merged config. `POST /api/v1/config` accepts all runtime-adjustable keys and applies them live.

### Feature flags (all disabled by default)

```yaml
backends_enabled: false

cost_tracking:
  enabled: false
  track_openrouter: true
  track_local: false

auto_summarization:
  enabled: false
  token_budget: 3000
  summary_model: "llama3.2:3b"
  keep_last: 4

flight_recorder:
  enabled: false
  retention_hours: 24
  max_records: 1000

conversation_replay:
  enabled: false

semantic_map:
  enabled: false
  similarity_threshold: 0.5
  max_topics: 50

orchestrator:
  enabled: false
  max_parallel_tasks: 5
  task_timeout_seconds: 120
  total_timeout_seconds: 300

hooks:
  - name: prompt_injection
    enabled: false
    mode: flag        # flag | block
    score_threshold: 2
```

### Runtime-only settings (runtime_config.yaml)

```yaml
runtime:
  default_model: ""
  force_route: ""            # simple | complex | code | large | ""
  border_threshold: null
  agentic_threshold: null
  tools_disabled: []
  system_prompt_prefix: ""
  web_ui_vi_mode: false
  web_ui_palette: "default"
  voice_enabled: false
  voice_hotkey: ""
  log_level: ""
```

---

## Docker Quickstart

```bash
cd docker
docker compose up -d
```

Pulls `llama3.2:3b` and `nomic-embed-text` on first start. Embedding centroids are built automatically in the background on first boot -- no manual step needed. Web UI at `http://localhost:8000`. Point Open WebUI at `http://localhost:8000/v1` with any non-empty API key.

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

### Done -- v1.0

- [x] OpenAI-compatible proxy, transparent streaming, SQLite + ChromaDB storage
- [x] Four-tier hybrid routing -- session cache, z-commands, embedding classifier, decision LLM
- [x] Embedding centroids auto-built at startup if missing (background task, non-blocking)
- [x] Multi-backend router, Ollama to OpenRouter failover
- [x] Cost tracking, streaming and non-streaming
- [x] Streaming latency tracking, model performance dashboard
- [x] Auto-summarization for context window management
- [x] All known OpenAI and Ollama endpoints proxied and logged
- [x] Catch-all passthrough for any unknown endpoint
- [x] LangChain ReAct operator agent
- [x] Parallel orchestrator (Operator tool)
- [x] Flight recorder, conversation replay, semantic map
- [x] Wire tap with persistent filters and live mode
- [x] Prompt injection detection, flag and block modes
- [x] Single-file web UI -- 8 tabs, full config editor, persistent state
- [x] Multi-pane chat with fan-out, per-pane targets, pagination
- [x] Harness tab -- Manual mode (parallel model/agent runner)
- [x] Harness tab -- Orchestrated mode (goal-directed master with plan/dispatch/evaluate loop)
- [x] Voice / push-to-talk -- mic button, click/hold/hotkey, STT forwarding
- [x] Full config exposure in web UI -- all sections, feature flag sub-options, live apply
- [x] Friendly error messages for disabled features with Config tab deep-links
- [x] Vi mode (zero bytes when disabled), palette themes, conversation forking
- [x] Busybox bb shell hardening in Docker

- [x] Generic OpenAI-compatible backend (llama.cpp, vLLM, TGI, Aphrodite, LocalAI)
- [x] Backend retry with exponential backoff on transient errors (404/429/5xx)
- [x] Multi-model ensemble voting -- judge LLM selects best response from N models
- [x] Web UI mobile responsive layout -- breakpoints for tablet, mobile, small phone, landscape
- [x] Runtime config bug fix -- feature flag toggling now correctly reads runtime_config first
- [x] Operator shell security hardening -- allowlist, dangerous pattern blocking, audit logging, busybox wrapper

### Next

- [ ] TTS wired into chat response pipeline (play assistant audio automatically)
- [ ] Conversation export to fine-tuning formats (JSONL, Alpaca, ShareGPT)
- [ ] System context injection (global prompt prefix via hot-reloadable system_context.md)
- [ ] Full parameter exposure via API and web UI (generation params, routing weights, ensemble config)

---

## Contributing

By contributing you agree your work will be licensed under AGPLv3, and you grant the maintainer(s) a perpetual license to sub-license under alternative commercial terms.

---

## License

AGPLv3. Enterprise use without copyleft -- reach out.

If you're making money off it, holler first. Four dogs, two kids, they eat a lot.

Otherwise free for everyone not making a buck from it. PRs and issues welcome if you've got something that fits the style.

---

*BeigeBox -- because the most interesting box on the network is the one nobody knows is there.*
