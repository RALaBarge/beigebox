# Stack Intervention & Extension Points - 2.0 Planning

**Date:** 2026-03-28
**Purpose:** Complete taxonomy of opportunities to modify/extend BeigeBox for novel data alteration during LLM interactions

---

## Part 1: Current Intervention Points (Request-Response Lifecycle)

### INPUT SIDE (Before Backend Request)

#### Command/Intent Routing
- Z-command parsing (`agents/zcommand.py`) — user-initiated instruction overrides
- Pre-request hooks (`hooks.py`) — arbitrary logic before send

#### Context Injection
- System context injection (`system_context.md`) — hot-reloaded system prompt
- Auto-summarization — conversation context management, message pruning
- Session cache lookups — serve different context based on session state

#### Request Mutation
- `_inject_generation_params()` — temperature, top_p, max_tokens overrides
- `_inject_model_options()` — per-model options from `config.yaml`
- `_apply_window_config()` — per-pane settings from request body (highest priority)
- Semantic cache lookup — serve cached response instead of calling backend

#### Routing/Dispatch
- Embedding classifier (Tier 3) — route based on semantic similarity to learned centroids
- Decision LLM (Tier 4) — route based on small LLM judgment
- Multi-backend router — A/B split weights, latency-aware deprioritization, failover order

### OUTPUT SIDE (After Backend Response)

#### Response Processing
- WASM transforms (`wasm_runtime.py`) — full streaming response mutation (stdin → stdout)
- Post-stream hooks — arbitrary logic after response received
- Tool execution interception — capture tool calls, modify results before re-emission

#### Storage/Caching
- Semantic cache storage — decide what gets cached (threshold tuning)
- Tool result cache — cache/replay tool outputs
- Conversation storage — what persists to SQLite

### RUNTIME/DYNAMIC

#### Feature Toggles
- `runtime_config.yaml` hot-reload — enable/disable subsystems per-request
- Model resource management — `force_reload` trigger (keep_alive:0 eviction), VRAM pressure responses
- Tool registry mutation — runtime plugin loading/unloading
- Hook registration — dynamic hook add/remove

---

## Part 2: Complete Stack Modification Opportunities

### REQUEST PIPELINE STAGES

#### Ingestion & Parsing
- Initial request validation
- API endpoint routing (which handler)
- Auth/permissions checking
- Request body parsing & validation
- Z-command detection & parsing
- Message extraction from request body

#### Pre-Processing
- Request normalization (canonicalize format)
- User context loading (who is this, what's their history)
- Session lookup
- Conversation history retrieval
- Request metadata extraction (length, complexity, intent signals)

#### Context Assembly
- Message history preparation
- Auto-summarization (existing)
- System context injection (existing)
- Dynamic context fetching (non-existent)
- Context ranking/prioritization
- Context truncation/pruning strategies

#### Request Mutation
- Z-command application
- Pre-request hooks execution
- Generation params injection
- Model-specific options injection
- Per-pane window config application
- Parameter validation & clamping
- Default value injection

#### Cache/Memory Lookups
- Session cache lookup
- Semantic cache probe
- Tool result cache lookup
- Conversation cache lookups
- Custom cache layer queries

#### Routing & Selection
- Embedding classifier (Tier 3)
- Decision LLM (Tier 4)
- Backend router selection
- Failover strategy
- A/B split assignment
- Load balancing

#### Backend-Specific Adaptation
- Backend-specific request transformation
- API schema normalization
- Backend feature capability checks
- Connection pool selection
- Timeout tuning per backend

#### Request Transmission
- Request signing/authentication
- Rate limiting application
- Retry policy setup
- Timeout configuration
- Streaming setup

#### Response Buffering & Processing
- Stream buffering (if WASM active)
- Chunk accumulation
- Token counting
- Latency measurement
- Response validation
- Error detection

#### Response Transformation
- WASM module execution (if active)
- Tool call interception
- Response format normalization
- Response enrichment (metadata attachment)
- Response repair/correction
- Token counting/accounting

#### Post-Processing
- Post-stream hooks execution
- Response quality assessment
- Confidence scoring
- Fallback/retry evaluation
- Response caching decision

#### Storage & Persistence
- Semantic cache storage
- Tool result cache storage
- Conversation persistence (SQLite)
- Metrics/telemetry storage
- Embedding index updates

---

### DATA STRUCTURES (Input/Output/Intermediate)

#### Request-Level
- Raw HTTP request
- Parsed request body
- Extracted messages list
- Request metadata (length, token count, intent)
- User context object
- Session state
- Z-command parsed result
- Request UUID/trace ID

#### Context-Level
- Message history (with summaries)
- System context string
- Dynamic context chunks
- Tool results/memories
- User preferences/overrides
- Backend-specific context

#### Backend-Level
- Selected backend identifier
- Backend connection params
- Backend-specific request format
- Backend routing metadata (why this backend)
- Backend performance profile
- Backend feature set

#### Response-Level
- Raw response stream
- Buffered response text
- Parsed response (if JSON)
- Tool calls (if present)
- Usage metadata (tokens, latency)
- Quality scores
- Error information
- Response UUID/trace ID

#### Metadata Envelopes (could add)
- Request classifier tags (intent, complexity, pattern)
- Routing decision trace (inputs, outputs, why)
- Response quality assessment (score, confidence, dimensions)
- Backend selection reasoning
- Tool execution trace
- Performance metrics per stage
- Error context & fallback info

---

### CONFIGURATION & POLICIES

#### Static Configuration (config.yaml)
- Backend definitions
- Model mappings
- Storage paths
- Security policies
- Feature flags (initial state)
- Tool registry paths
- WASM module registry

#### Runtime Configuration (runtime_config.yaml)
- Default model
- Temperature/top_p defaults
- Feature flag overrides
- Tool enablement
- Cache policies
- Routing strategies

#### Per-Request Configuration (_window_config)
- Model override
- Temperature override
- Token limit override
- Force reload flag
- Custom options object

#### Implicit Policies (could be explicit)
- Request routing strategy (when to use classifier vs decision LLM vs cache)
- Cache hit threshold (confidence required to return cached response)
- Fallback behavior (on error, on quality failure, on timeout)
- Retry strategy (when to retry, how many times, backoff)
- Rate limiting policy (per user, per model, per backend)
- Context truncation strategy (which parts to drop first)
- Tool execution policy (which tools allowed, execution limits)

---

### ROUTING & DECISION POINTS

#### Tier 1: Z-Commands (existing)
- Command parsing
- Command execution routing

#### Tier 2: Session Cache (existing, expandable)
- Cache hit detection
- Cache recency check
- Cache validity assessment

#### Tier 3: Embedding Classifier (existing, expandable)
- Centroid matching
- Similarity threshold check
- Fallback on low confidence

#### Tier 4: Decision LLM (existing, expandable)
- When to invoke
- Prompt construction
- Decision threshold
- Fallback behavior

#### Tier 5: Backend Router (existing, expandable)
- Primary backend selection (priority order)
- Latency-aware deprioritization
- A/B split assignment
- Health/availability checking
- Load balancing across backend instances

#### Routing Decision Inputs (could enrich)
- Request features (length, complexity, intent)
- User context (history, preferences, patterns)
- Conversation state (new vs ongoing, context freshness)
- Time of day / scheduling patterns
- Backend performance metrics (latency, error rate, VRAM)
- Model state (loaded, unloaded, capacity)
- Request type/pattern (cached request, tool-heavy, etc.)

---

### CACHING & MEMORY LAYERS

#### Session Cache (existing)
- What's stored
- Expiry policy
- Hit detection logic
- Update strategy

#### Semantic Cache (existing)
- Embedding model choice
- Similarity threshold
- Cache entry TTL
- Storage backend (ChromaDB)
- Query strategy

#### Tool Result Cache (existing)
- Tool call canonicalization
- Cache hit logic
- TTL per tool
- Invalidation strategy

#### Custom Caches (could add)
- Conversation pattern cache (recurring Q&A)
- Embedding cache (pre-computed embeddings)
- Backend response cache (raw backend outputs)
- Context chunk cache (frequently used context)
- Transformation cache (WASM output cache)

---

### TOOL SYSTEM

#### Tool Discovery & Registration
- Plugin loader
- Tool registry population
- Tool capability detection
- Tool availability checking

#### Tool Selection
- When to call which tool
- Tool execution ordering
- Parallel vs sequential execution
- Tool chaining logic

#### Tool Execution
- Tool invocation
- Timeout application
- Error handling
- Result capture
- Result caching decision

#### Tool Result Processing
- Result validation
- Result parsing
- Result injection back into context
- Result formatting for model

#### Tool Metadata
- Tool capabilities
- Tool constraints (max calls, latency, resources)
- Tool dependencies
- Tool success rate / reliability

---

### CONTEXT & PROMPT MANAGEMENT

#### Static System Context (existing: system_context.md)
- Content, format, injection point
- Hot-reload mechanism

#### Dynamic Context Providers (could add)
- Retrieve relevant examples
- Fetch documentation chunks
- Load user preferences
- Pull previous similar responses
- Query knowledge base
- Aggregate tool outputs

#### Context Composition (could add)
- Multi-source merging strategy
- Priority/ranking of context sources
- Deduplication
- Compression (abstractive vs extractive)
- Context relevance scoring

#### Prompt Templates (could add)
- Per-model prompt variants
- Per-intent prompt variants
- Adaptive prompt construction
- Prompt versioning

#### Context Window Management (existing partial)
- Auto-summarization (when to trigger, how)
- Message pruning (which messages to drop)
- Sliding window strategy
- Context priority assignment

---

### MODEL INTERACTION & ADAPTATION

#### Model Discovery
- Available models from Ollama
- Model capabilities detection
- VRAM requirements
- Context window sizes
- Latency profiles

#### Model Loading & Lifecycle
- Model loading strategy (lazy vs eager)
- Keep-alive tuning
- Force-reload triggers
- Unload strategy
- VRAM pressure response

#### Model-Specific Adaptation
- Model-specific parameter ranges
- Model-specific prompt format
- Model-specific performance tuning
- Model-specific error handling
- Model-specific fallbacks

#### Multi-Model Strategies (could add)
- Model selection per request type
- Model ensemble (call multiple, aggregate)
- Model cascading (try easy model first, escalate to capable model)
- Model specialization (different models for different tasks)

---

### RESPONSE QUALITY & VALIDATION

#### Response Validation
- Format validation (is it valid JSON/text)
- Completeness check (full response received)
- Semantic validity (does it make sense)
- Tool call validation (if present)

#### Quality Assessment (could add)
- Confidence scoring (how confident is the response)
- Coherence checking
- Factuality checking (against context)
- Relevance to query
- Hallucination detection
- Latency-based quality (fast vs slow)

#### Response Repair (could add)
- Error recovery (incomplete responses)
- Format fixing
- Content correction
- Clarification requests

#### Fallback & Retry (could add)
- Quality threshold for fallback trigger
- Retry strategy (same backend, different backend, different model)
- Graceful degradation (return partial response)
- Cache fallback (serve old response if new fails quality check)

---

### ERROR HANDLING & RESILIENCE

#### Error Detection
- Timeout detection
- Connection errors
- Rate limiting errors
- Malformed response detection
- Tool execution errors
- WASM transform errors

#### Error Classification (could add)
- Transient vs permanent
- Backend-specific vs generic
- User-actionable vs internal
- Retry-safe vs non-idempotent

#### Error Recovery (could add)
- Automatic retry with backoff
- Backend failover
- Degraded mode (serve cached if available)
- User-friendly error messages
- Error logging & alerting

---

### OBSERVABILITY & TELEMETRY

#### Request-Level Logging (existing partial)
- Request entry/exit
- Request ID/trace ID
- User ID
- Model/backend selected
- Routing decision

#### Routing Decision Logging (existing partial)
- Which tier made decision
- Decision inputs
- Decision output
- Confidence/scores

#### Performance Metrics
- Time per stage (routing, transformation, backend call, processing)
- Token counts (input, output)
- Backend latency
- E2E latency
- P95/P99 percentiles

#### Error Logging
- Error type
- Error context
- Recovery action taken
- User impact

#### Quality Metrics (could add)
- Response quality scores
- Tool execution success rate
- Cache hit rate
- Fallback frequency
- Retry frequency

---

### STATE & PERSISTENCE

#### Conversation Storage (existing: SQLite)
- Message history
- Conversation metadata
- Conversation state

#### User/Session State (could expand)
- User preferences
- User interaction patterns
- Session context
- Custom state types

#### Backend State (existing partial)
- Latency percentiles
- Error rates
- Model load state
- VRAM usage

#### Application State (AppState)
- Proxy instance
- Router instance
- Cache instances
- Store instances
- Hook registry
- Tool registry

---

### INTEGRATIONS & EXTERNAL SYSTEMS

#### Ollama Integration
- Model loading
- Keep-alive requests
- Stats/metrics queries
- Force-reload (keep_alive:0)

#### OpenRouter Integration (if enabled)
- API routing
- Rate limiting
- Cost tracking

#### Harness Integration (via injection queues)
- Run ID mapping
- Injection queue registration
- Message injection
- Result retrieval

#### Vector Store (ChromaDB)
- Embedding storage
- Semantic search
- Index updates

#### Tool System
- Tool plugin loading
- Tool registry queries
- Tool execution
- Result capture

---

### HOOKS & EXTENSIBILITY POINTS

#### Pre-Request Hooks (existing)
- Arbitrary logic before backend call
- Request mutation capability

#### Post-Stream Hooks (existing)
- Arbitrary logic after response
- Response mutation capability

#### Additional Hook Points (could add)
- Pre-routing hooks (before classifier)
- Per-backend hooks (backend-specific setup)
- Tool execution hooks (before/after tool call)
- Cache hooks (on hit, on miss, on store)
- Error hooks (on various error types)
- Quality check hooks
- Metric collection hooks

---

### FEATURE FLAGS & BEHAVIOR TOGGLES

#### Existing Feature Flags
- `decision_llm.enabled`
- `operator.enabled`
- `auto_summarization.enabled`
- `cost_tracking.enabled`
- `backends_enabled`
- `semantic_cache.enabled`
- `wasm.enabled`
- `web_ui.voice_enabled`

#### Potential Feature Flags (could add)
- Per-request routing strategy selection
- Quality validation enabled
- Response enrichment enabled
- Multi-model ensemble enabled
- Adaptive behavior learning enabled
- Fallback cascading enabled
- Tool execution metering enabled
- Context optimization enabled
- Error recovery enabled

---

### API ENDPOINTS & EXTERNAL INTERFACES

#### Existing Endpoints (main.py)
- `/v1/chat/completions` (main chat endpoint)
- `/v1/models` (model listing)
- `/health` (health check)
- `/inject` (harness injection)
- Others (web UI, etc.)

#### Instrumentation/Analytics Endpoints (could add)
- `/metrics` (performance metrics)
- `/routing-trace` (routing decisions)
- `/quality-assessment` (response quality)
- `/cache-stats` (cache performance)
- `/backend-stats` (backend performance)

---

## Summary: High-Leverage Extension Points

For 2.0 planning, the highest-leverage additions would be:

1. **Request/Response Transformation Layer** — systematic rewrites based on backend/context/request type
2. **Quality Validation Gates** — reject low-quality responses, trigger retries, fallbacks
3. **Dynamic Context Providers** — fetch/generate context on-demand vs static injection
4. **Request-Type Routing Strategies** — different logic for different query patterns
5. **Metadata Envelope System** — structured tagging of requests/responses throughout pipeline
6. **Error Recovery & Fallback Cascading** — intelligent recovery without user intervention
7. **Multi-Model Strategies** — ensemble, cascading, specialization per task type
8. **Adaptive Routing** — learn which backend works best for which patterns over time

