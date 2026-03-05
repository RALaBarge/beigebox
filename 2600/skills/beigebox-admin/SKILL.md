---
name: beigebox-admin
description: Administer a running BeigeBox instance — query conversation stats, inspect model performance, manage backends, search past conversations, and diagnose routing decisions. Use when the user asks about their BeigeBox setup, conversation history, model latency, costs, or backend health.
metadata:
  author: beigebox
  version: "1.0"
---

# BeigeBox Admin Skill

Instructions for administering a running BeigeBox instance.

## Available tools and when to use them

- **memory** — search past conversations by semantic similarity. Use for "did I ask about X before?" or "find conversations about Y".
- **web_search** — look up documentation, changelogs, or error messages not in local memory.
- **calculator** — compute cost estimates, token budgets, latency percentiles.
- **sysinfo** — check host resource usage (CPU, RAM, GPU) when diagnosing slow inference.

## Common tasks

### Check conversation stats
Use the `memory` tool to search for recent conversations. Summarize counts, topics, and any recurring themes.

### Diagnose slow responses
1. Call `sysinfo` to check current CPU/RAM/GPU load.
2. Check if a specific model is the bottleneck — note which model is configured as default.
3. Suggest: reduce `num_ctx`, enable `num_gpu` offload, or route heavy queries to a faster model.

### Explain routing decisions
BeigeBox uses a 4-tier routing pipeline:
1. Z-commands (user inline overrides: `z: complex`, `z: code`, etc.)
2. Agentic keyword scorer
3. Embedding classifier (cosine distance, ~50ms)
4. Decision LLM (small model judges borderline cases)

Session-sticky: once classified, a conversation stays on the same model.

### Cost management
- OpenRouter charges per token (prompt + completion separately).
- Check `cost_tracking.enabled` in config — if false, no data is being recorded.
- Local Ollama models cost $0 but still consume VRAM/RAM.

### Backend health
If a backend is marked degraded, its rolling P95 latency exceeded `latency_p95_threshold_ms`.
It will return to active rotation automatically when latency recovers.

## Key configuration paths

| What | Config key |
|---|---|
| Default model | `backend.default_model` |
| Multi-backend routing | `backends_enabled: true` + `backends:` list |
| Decision LLM | `decision_llm.enabled` + `decision_llm.model` |
| Cost tracking | `cost_tracking.enabled` |
| Semantic cache | `semantic_cache.enabled` |
| Stream stall timeout | `advanced.stream_stall_timeout_seconds` (default 30) |

## Z-commands cheat sheet

```
z: simple     → fast model
z: complex    → large model
z: code       → code model
z: search     → force web search
z: memory     → search past conversations
z: fork       → branch conversation
z: help       → list all commands
```

See references/api-endpoints.md for the full BeigeBox API reference.
