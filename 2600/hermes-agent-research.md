# Hermes Agent Research: Self-Learning Architecture

**Date:** 2026-03-26
**Source:** https://github.com/NousResearch/hermes-agent
**Focus:** Self-learning mechanisms and integration opportunities for beigebox

---

## Executive Summary

Hermes Agent uses a **closed-loop learning system with explicit (not automatic) self-improvement**. It combines trajectory capture, multi-store memory, session search, and optional RL training. Most valuable for beigebox: dual-memory pattern (bounded storage), frozen snapshot for caching, and trajectory logging infrastructure.

---

## Hermes Self-Learning Stack

### 1. Trajectory Capture & Training Data (trajectory.py)
- Saves all conversations to JSONL in ShareGPT format
- Maintains two files:
  - `trajectory_samples.jsonl` — successful conversations
  - `failed_trajectories.jsonl` — failed attempts
- Metadata per trajectory: timestamp, model used, completion status

**Trajectory Compressor** (`trajectory_compressor.py`):
- Post-processes trajectories for RL training
- Compresses middle turns while protecting first/last context
- Uses auxiliary models to summarize
- Produces training-ready data

### 2. RL Training Integration (tools/rl_training_tool.py)
- Integrates with **Tinker-Atropos** (RL training framework)
- Discovers environment configs via AST scanning
- Manages locked infrastructure settings (tokenizer, rollout server, WandB)
- Allows agent to edit training hyperparameters
- **Not automatic** — agent explicitly invokes this tool

### 3. Procedural Memory: Skills (tools/skill_manager_tool.py, skills_tool.py)
- **Skills** = procedural knowledge (HOW to do something)
- After complex tasks, agent creates/updates SKILL.md files in `~/.hermes/skills/`
- Skills = Markdown with YAML frontmatter + supporting files (references, templates, scripts)
- Progressive disclosure: metadata → full content → linked files
- Security scanning prevents injection attacks (skills_guard.py)

### 4. Persistent Memory: Dual-Store (tools/memory_tool.py) ⭐
Two separate stores with bounded limits:

**MEMORY.md (2,200 chars)**
- Agent's observations, learnings, discovered facts
- Example: "User prefers verbose explanations. Database runs on Postgres 15."
- Agent decides what's worth remembering vs forgetting

**USER.md (1,375 chars)**
- User profile: name, role, preferences, communication style, goals
- More curated/structured than MEMORY
- Example: "Data scientist. Prefers concise. Knows Python, learning Rust."

Key design features:
- **Bounded storage** — Forces prioritization
- **Frozen snapshot** — Injected at session start for prefix cache stability
  - Mid-session writes update files but DON'T change system prompt
  - Preserves Claude's KV cache
- **Atomic writes** — File-locked, deduped, no corruption on concurrent access
- **Security scanning** — Detects injection/exfiltration before accepting entries

### 5. Session Search & Cross-Session Learning (tools/session_search_tool.py)
- SQLite state database with FTS5 full-text search
- Agent can query: "When did I solve X? What approach did I use?"
- Cheap auxiliary LLM summarizes top N matching sessions
- Returns focused summaries, not raw transcripts (keeps context lean)
- Enables multi-session pattern recognition

### 6. User Modeling (tools/honcho_tools.py)
- Integrates with **Honcho** (external user context framework)
- Tools: `honcho_profile`, `honcho_search`, `honcho_context`
- Builds cumulative user profile via dialectic Q&A
- External service, but agent queries it to remember user

### 7. What's NOT Automatic
- No gradient-based learning on main model
- No preference learning loop (no systematic reward collection)
- No A/B testing framework
- Trainer must explicitly invoke RL training tool

---

## Architecture Patterns Relevant to BeigeBox

### Multi-Backend Routing (agent/smart_model_routing.py)
```python
# Simple keyword-based: if short & simple → cheap model, else → strong model
choose_cheap_model_route(user_message, routing_config)
→ resolves to: model, api_key, base_url, provider, runtime config
```

**Note:** BeigeBox already has sophisticated multi-tier routing (z-command → embedding classifier → decision LLM → backend router), so this is less relevant.

### State Management (hermes_state.py)
- SQLite state DB with session lifecycle tracking
- Message history: role, content, tool_calls, reasoning, token counts
- Session metadata: model, cost tracking, parent_session_id
- Auto-compress via ContextCompressor at 50% of context limit

### Prompt Caching Strategy (agent/prompt_caching.py)
- "System_and_3" strategy for Anthropic endpoints
- Cache breakpoints: system prompt (stable) + last 3 non-system messages
- Up to 4 cache_control markers
- Cache TTL: ephemeral (5min) or 1h

### Tool Coordination & Dispatch (model_tools.py, tools/registry.py)
- Central registry: all tools self-register at import
- `handle_function_call(tool_name, args)` dispatches to handler
- Each tool: JSON schema + Python handler
- 40+ tools available; agent selects subset via enabled_toolsets

### Agent Loop with Interruption (environments/agent_loop.py)
```
while not finished and turns < max_turns:
  response = llm.chat(messages, tools=tools)
  if response.tool_calls:
    for tool_call in response.tool_calls:
      result = handle_function_call(tool_call)
      messages.append(result)
  else:
    finished = True
```

### Delegation & Subagents (tools/delegate_tool.py)
- Spawn isolated child agents with restricted toolsets
- Each child: fresh conversation, own task_id, focused system prompt
- Parent blocks until children complete
- Blocked tools: delegate (no recursion), clarify (no user interaction)

### Mixture of Agents (tools/mixture_of_agents_tool.py)
- Reference models generate diverse responses in parallel
- Aggregator (strongest model) synthesizes final output
- For extremely hard problems needing intense reasoning

---

## Integration Opportunities for BeigeBox

### Immediate (High-Value, Low-Effort)

| Pattern | Use Case | Hermes Source | BeigeBox Application |
|---------|----------|---------------|----------------------|
| **Trajectory logging** | Analytics + RL data | `trajectory.py` | Log all requests/responses with metadata (user, backend, cost, latency, tokens) |
| **Dual-memory store** | Personalization + learning | `memory_tool.py` | Per-user profile (2KB) + agent-learned facts (2KB): "backend X fails on tool calls", "user prefers model Y" |
| **Session search** | Pattern reuse, cost reduction | `session_search_tool.py` | FTS5 search past requests → suggest cached responses or adapt approach |
| **Prompt caching** | Cost reduction | `prompt_caching.py` | Cache stable parts (system prompt, user context) across requests |
| **Frozen snapshot** | Cache stability | `memory_tool.py` design | Load user context once at request start; don't change mid-stream (keeps semantic cache hot) |

### Medium-Term (Self-Learning Foundation)

1. **Cost Tracking & Analytics**
   - Per-user spending, per-model costs, trends
   - Detect cost anomalies
   - Feed into routing decisions (similar to `hermes_state.py`)

2. **Feedback Collection**
   - Capture user thumbs-up/thumbs-down on responses
   - Store with request fingerprint → signal for preference learning

3. **Pattern Learning**
   - Cluster similar requests
   - Identify successful backends per request type
   - Suggest model/backend combinations

### Advanced (Self-Learning Loop)

1. **RL Training Integration**
   - Export trajectories for Tinker-Atropos fine-tuning
   - Fine-tune local model on common request types
   - Route to fine-tuned model if similarity score high

2. **Skill Creation**
   - If complex request succeeds with tool chain, save as "skill"
   - Similar future requests bypass chain, call skill directly
   - Skills = stored prompts/tool sequences

---

## Key Design Insights from memory_tool.py ⭐

The most valuable pattern for beigebox:

### Bounded Storage Forces Prioritization
- Don't store everything; decide what's worth remembering
- Examples that matter: "Model X is 2x faster on tool calls", "User always wants JSON output"
- Examples that don't: "User ran 47 queries last week"

### Frozen Snapshot for Cache Optimization
- Load user context once at request start
- Don't change it mid-stream (preserves LLM KV cache)
- Mid-session writes update files but don't touch system prompt
- Clever: separates storage updates from cache invalidation

### Dual Stores with Different Retention
- **MEMORY.md** — Agent-learned domain facts (2,200 chars)
  - Routing hints, system behaviors, discovered patterns
  - TTL: let old facts expire naturally
- **USER.md** — User profile (1,375 chars)
  - Goals, preferences, constraints
  - TTL: longer, user can explicitly update

### Atomic Writes
- File-locked, deduped, no race conditions
- Security scanning for injection/exfiltration
- Production-grade reliability

---

## Implementation Roadmap for BeigeBox

### Phase 1: Observability (2-3 days)
1. Trajectory logging: save all requests/responses to JSONL
2. Basic cost tracking: per-user, per-model, per-backend
3. User profile store: 2KB bounded, atomic writes

### Phase 2: Learning Signals (1 week)
1. Session search: FTS5 across all user requests
2. Feedback collection: thumbs-up/down buttons
3. Pattern detection: clustering similar requests

### Phase 3: Adaptive Routing (2 weeks)
1. Feed cost/latency/success signals back to MultiBackendRouter
2. Prefer backends that succeeded on similar requests
3. Cost-aware routing: route to cheap models when budget allows

### Phase 4: RL Training (4+ weeks)
1. Compress trajectories (protect head/tail, summarize middle)
2. Export to Tinker-Atropos format
3. Fine-tune local model on common request types
4. Route similar requests to fine-tuned model

---

## What BeigeBox Already Has vs Hermes

| Capability | BeigeBox | Hermes |
|------------|----------|--------|
| Multi-tier routing | ✅ (z-command → classifier → decision LLM → router) | ✅ (simple keyword-based) |
| Latency-aware routing | ✅ (P95 tracking) | ❌ |
| A/B splitting | ✅ | ❌ |
| Trajectory logging | ❌ | ✅ |
| User profile store | ❌ | ✅ (memory_tool.py) |
| Session search | ❌ | ✅ (FTS5) |
| Cost tracking | ❌ | ❌ (but possible via trajectory) |
| RL training integration | ❌ | ✅ (Tinker-Atropos) |
| Skill creation | ❌ | ✅ |
| Prompt caching strategy | ❌ | ✅ |

---

## Recommended Starting Point

1. **Copy trajectory.py pattern** → save all requests/responses with metadata
2. **Implement memory_tool.py pattern** → bounded per-user profile + learned facts
3. **Add FTS5 session search** → find similar past requests
4. **Extend SmartModelRouter** → use cost signals from trajectories
5. **Then layer on RL training** (rl_training_tool.py pattern)

This gives immediate observability + basic self-improvement without heavy lifting.

---

## Files Worth Examining in Hermes

| File | Lines | Purpose |
|------|-------|---------|
| `run_agent.py` | 7,740 | Main agent loop, message history, tool orchestration |
| `agent/trajectory.py` | 57 | Save conversations for training |
| `trajectory_compressor.py` | 400+ | Post-process trajectories for RL |
| `tools/rl_training_tool.py` | 400+ | RL training orchestration |
| `tools/memory_tool.py` | 549 | Persistent memory with file locking ⭐ |
| `tools/skill_manager_tool.py` | 400+ | Agent-managed skill creation |
| `agent/context_compressor.py` | 300+ | Auto-compress long conversations |
| `agent/smart_model_routing.py` | 197 | Keyword-based routing |
| `agent/prompt_caching.py` | 73 | Cache strategy for Anthropic |
| `hermes_state.py` | 500+ | SQLite session store, FTS5 search |
| `tools/session_search_tool.py` | 300+ | Cross-session recall |
| `environments/agent_loop.py` | 300+ | Tool-calling loop, reasoning extraction |
| `tools/delegate_tool.py` | 300+ | Subagent spawning |
| `agent/auxiliary_client.py` | 600+ | Multi-provider client resolution |
