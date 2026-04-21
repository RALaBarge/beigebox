# BeigeBox Model Roles & Profiles

This document explains what each model in BeigeBox is used for, so you can swap them at runtime.

---

## Model Profiles (The 4 Roles)

### 1. **DEFAULT** — General Conversation
**What it does:** Used for every chat request unless you override it  
**When it's called:** Every `/v1/chat/completions` request  
**Example:** User asks "write a poem" → uses default model  
**Frequency:** VERY HIGH (every user message)  
**Config path:** `models.default`  
**Current value:** `gemma3:4b` (config.yaml) / `qwen3:4b` (docker)

**How to override at runtime:**
```bash
curl -X POST http://localhost:8001/v1/chat/completions \
  -d '{"model": "qwen3:30b", "messages": [...]}'
# Ignores config.yaml default, uses qwen3:30b instead
```

---

### 2. **ROUTING** — Smart Backend Picker (Decision LLM)
**What it does:** Decides which backend to use for ambiguous requests  
**When it's called:** Only when routing is unclear (decision_llm.enabled: true)  
**Example:** Request says "big model" → routing LLM decides OpenRouter vs local  
**Frequency:** MEDIUM (depends on ambiguity of request)  
**Config path:** `models.profiles.routing`  
**Current value:** `gemma3:4b` (config.yaml) / `llama3.2:3b` (docker) ⚠️ MISMATCH

**How it works:**
```
User: "I need a powerful model for analysis"
         ↓
Is this clearly local or cloud? → No, ambiguous
         ↓
Call routing model: "Should this go to OpenRouter or local Ollama?"
         ↓
Routing model (llama3.2:3b) analyzes and says → "Use OpenRouter" 
         ↓
Request forwarded to cloud backend
```

**When to change:** If routing is slow, use a smaller/faster model. If routing is wrong, use a larger model.

---

### 3. **AGENTIC** — Tool Use Agent (Operator)
**What it does:** Runs multi-step agent loops with tool calling  
**When it's called:** When `operator.enabled: true` and request asks for action  
**Example:** "Search the web AND analyze the results" → agentic model executes both steps  
**Frequency:** LOW to MEDIUM (depends on whether operator is enabled)  
**Config path:** `models.profiles.agentic`  
**Current value:** `gemma3:4b` (config.yaml) / `qwen3:4b` (docker)

**How it works:**
```
User: "Search for XYZ and summarize"
         ↓
Operator enabled? Yes
         ↓
Agentic model (qwen3:4b) thinks: 
  "Step 1: I need to call web_search tool"
  "Step 2: I need to summarize the results"
         ↓
Executes: web_search("XYZ") → returns 10 articles
         ↓
Summarize results using the same agentic model
         ↓
Return summary to user
```

**When to change:** If agentic tasks are slow or fail, try a different model. Tool use works better with models trained on function calling.

---

### 4. **SUMMARY** — Context Compression
**What it does:** Compresses long conversation histories when context gets too long  
**When it's called:** Only when `auto_summarization.enabled: true` and history > token_budget  
**Example:** 20-turn conversation gets compressed to bullet points  
**Frequency:** LOW (only when history exceeds limit)  
**Config path:** `models.profiles.summary`  
**Current value:** `gemma3:4b` (config.yaml) / `qwen3:4b` (docker)

**How it works:**
```
Conversation history: 50,000 tokens (too long!)
         ↓
Token budget exceeded? Yes, need to compress
         ↓
Summary model (qwen3:4b) compresses:
  20 turns → 5 bullet points
         ↓
Compressed history: 5,000 tokens
         ↓
Continue conversation with smaller history
```

**When to change:** If summaries lose important context, use a larger model. If summarization is slow, use a smaller/faster one.

---

## Quick Reference Table

| Profile | Purpose | Frequency | Overhead | Recommendation |
|---------|---------|-----------|----------|---|
| **default** | General chat | VERY HIGH | Highest | Fast & capable (4-7B) |
| **routing** | Pick backend | MEDIUM | Low | Small & fast (3B) |
| **agentic** | Multi-step tool use | MEDIUM | Medium | Good at function calling |
| **summary** | Compress context | LOW | Low | Doesn't matter much |

---

## Current Configuration Issues

### Problem 1: config.yaml uses all `gemma3:4b`
```yaml
models:
  default: "gemma3:4b"
  profiles:
    routing: "gemma3:4b"      # ← Should be smaller/faster
    agentic: "gemma3:4b"
    summary: "gemma3:4b"
```

**Issue:** Routing decisions are as expensive as a full chat. Should use a smaller model.

### Problem 2: docker/config.docker.yaml has different values
```yaml
models:
  default: "qwen3:4b"
  profiles:
    routing: "llama3.2:3b"    # ← Different from config.yaml!
    agentic: "qwen3:4b"
    summary: "qwen3:4b"
```

**Issue:** Docker stack uses different routing model than production.

### Problem 3: Inconsistent across 3 config files
- `config.yaml`: gemma3 for everything
- `docker/config.docker.yaml`: qwen3 + llama3.2 for routing
- `config.example.yaml`: qwen3 for everything

---

## Recommended Model Assignments (by capability)

### Option 1: CPU-Friendly (For Ollama local)
```yaml
models:
  default: "qwen3:4b"              # General chat (1.3GB VRAM)
  profiles:
    routing: "llama3.2:1b"         # Fast routing (200MB VRAM)
    agentic: "qwen3:4b"            # Tool use (1.3GB VRAM)
    summary: "llama3.2:1b"         # Compression (200MB VRAM)
```
**Total VRAM:** ~3GB | **Total Models:** 2 (qwen3:4b + llama3.2:1b)

### Option 2: Balanced (For Ollama with 8GB VRAM)
```yaml
models:
  default: "qwen3:7b"              # Better general chat (3GB VRAM)
  profiles:
    routing: "llama3.2:3b"         # Smart routing (1.5GB VRAM)
    agentic: "qwen3:7b"            # Good at tools (3GB VRAM)
    summary: "llama3.2:1b"         # Lightweight compression (200MB VRAM)
```
**Total VRAM:** ~5GB | **Total Models:** 3 (qwen3:7b + llama3.2:3b + llama3.2:1b)

### Option 3: Cloud-First (For OpenRouter + local fallback)
```yaml
models:
  default: "gpt-4-turbo"           # Cloud models, best quality
  profiles:
    routing: "gpt-3.5-turbo"       # Cheaper routing
    agentic: "gpt-4"               # Better tool use
    summary: "gpt-3.5-turbo"       # Cheap compression
```
**Cost:** ~$0.10 per request | **Local VRAM:** 0GB (all cloud)

---

## How to Override at Runtime

### Option A: Swap Default Model
```bash
curl -X POST http://localhost:8001/v1/chat/completions \
  -d '{
    "model": "qwen3:30b",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### Option B: Use Z-Command (BeigeBox extension)
```bash
# Request with inline override
# Message: "z: model=qwen3:30b"
# (if z-commands enabled in config)
```

### Option C: Override Routing Model (runtime_config.yaml)
```yaml
# data/runtime_config.yaml
models_routing: "llama3.2:3b"  # Override for this session
```

### Option D: Temporarily Enable/Disable Features
```yaml
# data/runtime_config.yaml
features_operator: false        # Disable agentic mode (no tool use)
features_decision_llm: false    # Disable routing LLM (use static routing)
```

---

## Model Selection Tips

### For Routing (fastest = best)
- ✅ llama3.2:1b — Ultra-fast, good enough for most decisions
- ✅ llama3.2:3b — Balanced, can handle complex routing
- ❌ qwen3:30b — Way too expensive for routing decisions

### For Agent/Tools
- ✅ qwen3:4b — Good at function calling
- ✅ gpt-4 — Excellent at tool use
- ❌ llama3.2:1b — Too small for multi-step reasoning

### For Summarization
- ✅ Any model works (frequency is low)
- ✅ Smaller models are fine (llama3.2:1b)
- ✅ Fast models preferred (reduces latency)

### For General Chat
- ✅ What users ask for (they'll override anyway)
- ✅ Balanced option (4-7B models)
- ❌ Don't guess — config default is just a fallback

---

## Current State Analysis

**Your setup:** 3 different config files with different models

```
config.yaml (production?)          → all gemma3:4b
    ↓
docker/config.docker.yaml (docker) → qwen3:4b + llama3.2:3b routing
    ↓
config.example.yaml (template)     → qwen3:4b everywhere
```

**Which one is actually used?**
- If running Docker: `docker/config.docker.yaml`
- If running locally: `config.yaml`
- Users who copy template: `config.example.yaml`

**Result:** Model behavior is unpredictable depending on how you deployed.

---

## Next Steps

1. **Decide:** Which models do you actually want to use?
2. **Consolidate:** Update all 3 config files to match
3. **Test:** Verify the chosen models work for your use cases
4. **Document:** Add a `MODELS.md` so others know what's being used
5. **Lock:** Once decided, make these immutable (commit to git, don't change)
