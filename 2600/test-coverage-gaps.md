# BeigeBox Test Coverage Analysis & Suggested Tests

**Date**: February 21, 2026  
**Status**: Gap Analysis  
**Scope**: v0.9.2 release

---

## Current Test Summary

**Total test functions**: ~154 across 16 files  
**Coverage by module**:
- ✅ Strong: Backends (15), Tools (15), V0.8 features (30), Web UI (23)
- ⚠️ Medium: Costs (9), Storage (7), Decision (6), Hooks (6), Replay (7)
- ❌ Weak: Proxy (3), Tools registry (2)
- ❌ Missing: Entire subsystems

---

## Critical Gaps

### Gap 1: Proxy Layer (Only 3 Tests) 🔴

**Location**: `tests/test_proxy.py`  
**Current tests**: Message creation, OpenAI format, token count  
**Missing**: Everything about the actual proxy behavior

**Suggested tests**:

1. **Routing decision tests**
   - Test that `/v1/chat/completions` with different prompts triggers appropriate routing decisions
   - Test session cache behavior (sticky model within conversation)
   - Test that z-commands bypass routing
   - Test embedding classifier is consulted when config enables it
   - Test decision LLM is called for borderline cases
   - Test agentic scorer detects tool-use intent

2. **Wiretap logging tests**
   - Test that every message type (chat, operator, decision) gets logged to wire.jsonl
   - Test wire log format (role, direction, model, conversation_id fields populated)
   - Test wire log truncation (large messages get truncated to sanity limit)
   - Test wire log includes tool invocations (tool=name field)
   - Test backward compatibility: old wire.jsonl can still be read

3. **Hook execution tests**
   - Test pre-request hooks are called before forwarding
   - Test post-response hooks are called after backend response
   - Test hooks can modify body before sending to backend
   - Test hooks can modify response before returning to client
   - Test hook exceptions are caught and logged (don't break pipeline)

4. **Stream handling tests**
   - Test that streaming responses are properly handled
   - Test that stream=false returns full response
   - Test that stream=true returns SSE stream
   - Test streaming error handling (backend dies mid-stream)

5. **Cost tracking in proxy tests**
   - Test that costs are tracked during forwarding
   - Test OpenRouter cost sentinel is parsed from streaming
   - Test token counts are estimated when not provided

6. **Backend failover tests**
   - Test that request fails over to secondary backend if primary is down
   - Test that cost tracking still works with failover
   - Test that conversation is stored even with failover

7. **Conversation storage in proxy tests**
   - Test that user message is stored immediately
   - Test that assistant response is stored with model/cost/latency
   - Test that model is correct (routed model, not default)
   - Test that token count is stored

---

### Gap 2: Harness Orchestrator (No Tests) 🔴

**Location**: `beigebox/agents/harness_orchestrator.py`  
**Current tests**: None  
**Missing**: Everything

**Suggested tests**:

1. **Event stream generation**
   - Test that `run()` yields events in correct order: start → plan → dispatch → result → evaluate → finish
   - Test that each event has required fields (type, ts, round)
   - Test that error events are generated on failure
   - Test that finish event includes final_answer and rounds count

2. **Plan generation**
   - Test that plan breaks goal into tasks
   - Test that tasks are assigned to available targets
   - Test that reasoning is included
   - Test that plan respects max_tasks_per_round limit

3. **Dispatch and result collection**
   - Test that all tasks in plan are executed
   - Test that results include latency_ms
   - Test that failed tasks are marked with error status
   - Test that task_stagger_seconds is respected (tasks don't all fire simultaneously)

4. **Evaluation logic**
   - Test that evaluation sees all previous results
   - Test that evaluation decides "continue" vs "finish"
   - Test that reasoning for decision is included
   - Test that final answer is included in finish event

5. **Round capping**
   - Test that max_rounds is respected
   - Test that when capped, finish event has capped=true
   - Test that enough information is gathered to produce answer even if capped

6. **Model override**
   - Test that model parameter overrides config default
   - Test that model is used for planning and evaluation

7. **Target validation**
   - Test that invalid targets are rejected
   - Test that target names are passed correctly to workers

---

### Gap 3: Operator Agent (No Tests) 🔴

**Location**: `beigebox/agents/operator.py`  
**Current tests**: None  
**Missing**: Everything about agent execution

**Suggested tests**:

1. **Tool invocation**
   - Test that operator can call available tools
   - Test that unavailable tools are rejected
   - Test that tool results are passed back to LLM

2. **Conversation flow**
   - Test that question → thought → action → observation loop works
   - Test that agent eventually reaches final answer
   - Test that agent respects max_iterations

3. **Tool fallback**
   - Test that if tool fails, agent handles gracefully
   - Test that agent can retry with different tool if first fails
   - Test that agent eventually gives up and returns partial answer

4. **System info tool integration**
   - Test that system_info tool runs basic commands
   - Test that shell command execution respects allowlist
   - Test that blocked commands are rejected
   - Test that GPU/CPU/memory info is gathered

5. **Memory tool integration**
   - Test that memory tool can search past conversations
   - Test that results are formatted for LLM context
   - Test that only relevant conversations are returned

6. **Error handling**
   - Test that backend unavailable is handled
   - Test that max_iterations prevents infinite loops
   - Test that partial answer is returned on timeout

---

### Gap 4: Config Loading & Validation (No Tests) 🔴

**Location**: `beigebox/config.py`  
**Current tests**: None  
**Missing**: Config parsing, validation, merging

**Suggested tests**:

1. **Config file loading**
   - Test that config.yaml is loaded correctly
   - Test that runtime_config.yaml overrides config.yaml
   - Test that missing files use defaults
   - Test that malformed YAML is caught

2. **Config merging**
   - Test that runtime_config fields override static config
   - Test that new runtime fields are added correctly
   - Test that old fields are preserved

3. **Config validation**
   - Test that required fields are present
   - Test that invalid types are caught
   - Test that default values are used when missing
   - Test that enum values are validated (log_level, etc.)

4. **Feature flags**
   - Test that all feature flags default to false
   - Test that feature flags can be enabled
   - Test that disabling a feature is safe (no crashes)

5. **Backend URL parsing**
   - Test that URLs are normalized (trailing slashes removed)
   - Test that invalid URLs are caught
   - Test that localhost vs network addresses are handled

---

### Gap 5: CLI Commands (No Tests) 🔴

**Location**: `beigebox/cli.py`  
**Current tests**: None  
**Missing**: Command execution, argument parsing

**Suggested tests**:

1. **Help command**
   - Test `beigebox --help` returns usage
   - Test `beigebox dial --help` returns command help

2. **Dial (start) command**
   - Test that `beigebox dial` starts server
   - Test that port is configurable
   - Test that config is loaded
   - Test that health check endpoint is available

3. **Tap (log) command**
   - Test that `beigebox tap` reads wire.jsonl
   - Test that filtering works (role, direction)
   - Test that live mode polls for new entries
   - Test that raw mode outputs JSON

4. **Flash (stats) command**
   - Test that `beigebox flash` queries stats
   - Test that output format is readable
   - Test that model performance is included

5. **Operator command**
   - Test that `beigebox operator "question"` runs agent
   - Test that result is returned
   - Test that tools are available

---

### Gap 6: Vector Store / Embeddings (No Tests) 🔴

**Location**: `beigebox/storage/vector_store.py`  
**Current tests**: None  
**Missing**: ChromaDB operations, similarity search

**Suggested tests**:

1. **Message embedding & storage**
   - Test that messages are embedded
   - Test that embeddings are stored to ChromaDB
   - Test that old embeddings can be retrieved

2. **Semantic search**
   - Test that similar messages are ranked by cosine similarity
   - Test that threshold filtering works
   - Test that search results include score

3. **Collection management**
   - Test that collections are created per conversation
   - Test that duplicate embeddings don't get stored
   - Test that deleting a conversation removes its embeddings

4. **Embedding model loading**
   - Test that embedding model is loaded at startup
   - Test that model failures are handled gracefully
   - Test that embeddings still work if model times out

---

### Gap 7: Summarization (No Tests) 🔴

**Location**: `beigebox/summarizer.py`  
**Current tests**: None  
**Missing**: Summary generation, context window management

**Suggested tests**:

1. **Summary generation**
   - Test that long conversation is summarized
   - Test that summary captures key points
   - Test that summary is shorter than original

2. **Context window management**
   - Test that auto_summarization is triggered when context > token_budget
   - Test that last N messages are kept even after summarization
   - Test that summary is inserted as system message

3. **Summary format**
   - Test that summary is valid text
   - Test that summary preserves conversation structure (who said what)

4. **Integration with chat**
   - Test that summarized conversation still works
   - Test that follow-up messages work after summary

---

### Gap 8: Wiretap & Logging (Limited Tests) 🟡

**Location**: `beigebox/wiretap.py`  
**Current tests**: Minimal  
**Missing**: Logging format, filtering, persistence

**Suggested tests**:

1. **Wire log format**
   - Test that JSONL format is correct (valid JSON per line)
   - Test that all required fields are present
   - Test that content is truncated at 2000 chars
   - Test that timestamps are ISO format

2. **Filtering**
   - Test that role filter works (only user/assistant messages)
   - Test that direction filter works (only inbound/outbound)
   - Test that filters can be combined

3. **Live mode**
   - Test that file is polled periodically
   - Test that new entries are shown immediately
   - Test that file rotation is handled (if log rolls over)

4. **Tap endpoint**
   - Test that `/api/v1/tap` returns recent entries
   - Test that filters are applied
   - Test that limit parameter works

---

### Gap 9: Semantic Map (Limited Tests) 🟡

**Location**: `beigebox/semantic_map.py`  
**Current tests**: 13 tests  
**Missing**: Specific edge cases

**Suggested tests**:

1. **Large conversation handling**
   - Test that 1000+ message conversation can be mapped
   - Test that memory doesn't explode

2. **Sparse similarities**
   - Test that conversations with no similar messages are handled
   - Test that isolated clusters are shown

3. **Topic naming**
   - Test that inferred topic names are reasonable
   - Test that generic conversations get generic names

4. **Export formats**
   - Test that export works (JSON, GraphML, etc.)
   - Test that re-importing exported data works

---

### Gap 10: Embedding Classifier (No Tests) 🔴

**Location**: `beigebox/agents/embedding_classifier.py`  
**Current tests**: None  
**Missing**: Classification logic, centroid operations

**Suggested tests**:

1. **Centroid building**
   - Test that centroids are built at startup if missing
   - Test that centroids are cached
   - Test that rebuilding centroids works
   - Test that centroid rebuild is non-blocking

2. **Classification**
   - Test that "simple" prompts classify as simple
   - Test that "complex" prompts classify as complex
   - Test that "code" prompts classify as code
   - Test that borderline prompts score < confidence threshold

3. **Confidence scores**
   - Test that clear cases have high confidence
   - Test that ambiguous cases have low confidence
   - Test that low confidence falls through to decision LLM

4. **Multi-class support**
   - Test that all 4 classes (simple/complex/code/creative) work
   - Test that custom routes work

---

### Gap 11: Agentic Scorer (No Tests) 🔴

**Location**: `beigebox/agents/agentic_scorer.py`  
**Current tests**: None  
**Missing**: Pattern matching logic

**Suggested tests**:

1. **Tool detection**
   - Test that "search for" triggers web_search flag
   - Test that "calculate" triggers calculator flag
   - Test that "what time is it" triggers datetime flag

2. **False positives**
   - Test that legitimate text doesn't trigger flags
   - Test that case insensitivity works

3. **Multiple intents**
   - Test that message can have multiple intents
   - Test that all intents are detected

---

### Gap 12: Multi-Backend Router (Limited Tests) 🟡

**Location**: `beigebox/backends/router.py`  
**Current tests**: ~8 in test_backends.py  
**Missing**: Priority-based failover, health checks

**Suggested tests**:

1. **Health check monitoring**
   - Test that dead backend is removed from rotation
   - Test that recovered backend is added back
   - Test that health checks happen periodically

2. **Priority ordering**
   - Test that higher priority backend is tried first
   - Test that lower priority is only used if higher fails

3. **Sticky backend**
   - Test that once a backend is selected, it's used for retries
   - Test that backend change is logged

4. **Cost comparison**
   - Test that cheaper backend is preferred if performance is similar
   - Test that more expensive backend is used if it's faster

---

### Gap 13: Model Advertising (Limited Tests) 🟡

**Location**: `beigebox/backends/ollama.py` or where models are listed  
**Current tests**: 6 tests in test_model_advertising.py  
**Missing**: Integration with routing

**Suggested tests**:

1. **Model caching**
   - Test that model list is cached
   - Test that cache is refreshed periodically
   - Test that new model appears in list after pull

2. **Model filtering**
   - Test that embedding models are filtered from chat models
   - Test that vision models are marked correctly
   - Test that deprecated models are hidden

3. **Model metadata**
   - Test that model size is reported
   - Test that model capabilities are exposed
   - Test that model is marked as available/unavailable

---

### Gap 14: End-to-End Integration Tests 🔴

**Location**: `tests/`  
**Current tests**: None  
**Missing**: Real workflows

**Suggested tests**:

1. **Chat flow**
   - User sends chat message
   - Message is routed
   - Decision is made
   - Backend is called
   - Response is returned
   - Conversation is stored
   - Cost is tracked
   - Wire log is updated

2. **Operator flow**
   - User asks operator a question
   - Operator chooses tools
   - Tools are executed
   - Results are returned
   - LLM synthesizes answer

3. **Harness flow**
   - User sends goal to harness
   - Master plans tasks
   - Tasks are dispatched to workers
   - Results are collected
   - Master evaluates
   - Rounds continue until done
   - Final answer is returned

4. **Multi-backend fallover**
   - Primary backend is unreachable
   - Request fails over to secondary
   - Cost is attributed to secondary
   - Conversation is stored

---

### Gap 15: Error Scenarios 🔴

**Location**: Throughout codebase  
**Current tests**: Few explicit error tests  
**Missing**: Error handling paths

**Suggested tests**:

1. **Backend errors**
   - Backend returns 500
   - Backend times out
   - Backend is unreachable
   - Backend returns malformed JSON

2. **Storage errors**
   - SQLite database is locked
   - ChromaDB is unreachable
   - Disk is full
   - Query returns unexpected format

3. **LLM errors**
   - Decision LLM times out
   - Decision LLM returns invalid JSON
   - Decision LLM is out of tokens
   - Embedding model is unavailable

4. **Config errors**
   - Invalid YAML syntax
   - Missing required fields
   - Invalid enum values
   - File doesn't exist

5. **Resource exhaustion**
   - Too many conversations
   - Message is too large
   - Image file is too large
   - Request queue is full

---

### Gap 16: Security/Safety Tests 🔴

**Location**: `tests/test_hooks.py` (partial)  
**Current tests**: Minimal  
**Missing**: Attack scenarios

**Suggested tests**:

1. **Prompt injection detection**
   - Test that "Ignore previous instructions" is flagged
   - Test that "You are now in developer mode" is blocked
   - Test that jailbreak patterns are detected
   - Test that encoded injections (base64) are caught

2. **Command injection in operator**
   - Test that `ls; rm -rf /` is blocked
   - Test that pipe chaining is blocked
   - Test that backticks are blocked
   - Test that $(command) substitution is blocked

3. **Path traversal**
   - Test that `../../../etc/passwd` is rejected
   - Test that absolute paths outside data dir are rejected
   - Test that symlinks don't bypass restrictions

4. **Resource limits**
   - Test that request timeout is enforced
   - Test that large responses are truncated
   - Test that memory usage doesn't explode

---

## Test File Organization Recommendations

### Consider splitting large test files

- `test_v08.py` (400 lines) → `test_fork_conversation.py` + `test_prompt_injection.py`
- `test_web_ui.py` (334 lines) → `test_ui_chat_pane.py` + `test_ui_operator_tab.py`

### Create new test files for major gaps

- `test_proxy_routing.py` — Routing decision, tier selection, session cache
- `test_harness_orchestrator.py` — Event stream, planning, dispatch, evaluation
- `test_operator_agent.py` — Tool invocation, conversation flow, error handling
- `test_embedding_classifier.py` — Centroid building, classification, confidence
- `test_wiretap.py` — Log format, filtering, live mode
- `test_vector_store.py` — Embedding storage, semantic search, collections
- `test_summarizer.py` — Summary generation, context window, integration
- `test_cli.py` — Command execution, argument parsing
- `test_config.py` — Loading, validation, merging
- `test_agentic_scorer.py` — Tool detection, false positives
- `test_security.py` — Prompt injection, command injection, path traversal
- `test_e2e.py` — End-to-end workflows (chat, operator, harness, failover)

---

## Testing Priorities

### Tier 1 (Critical Path) — This Sprint

1. **Proxy routing tests** (gap 1) — Core functionality
2. **Harness orchestrator tests** (gap 2) — New feature in v1.0
3. **Operator agent tests** (gap 3) — User-facing feature
4. **Security tests** (gap 16) — Safety critical

### Tier 2 (Important) — Next Sprint

5. **Config tests** (gap 4) — Infrastructure
6. **Embedding classifier tests** (gap 10) — Routing backbone
7. **Vector store tests** (gap 6) — Data backbone
8. **E2E integration tests** (gap 14) — Validation

### Tier 3 (Nice to Have) — Later

9. CLI tests (gap 5)
10. Wiretap tests (gap 8)
11. Agentic scorer tests (gap 11)
12. Summarizer tests (gap 7)
13. Semantic map edge cases (gap 9)
14. Multi-backend router failover (gap 12)

---

## Notes on Test Types

- **Unit tests**: Test single function/class in isolation
- **Integration tests**: Test multiple components together
- **E2E tests**: Test full request flow from API to database
- **Error scenario tests**: Test failure paths and recovery
- **Security tests**: Test attack vectors and safety boundaries
- **Performance tests**: Test scalability and resource usage (separate from unit tests)

---

## Mocking Strategy

**What to mock**:
- HTTP backends (httpx requests)
- LLMs (decision agent, operator responses)
- External services (web search, Google API)

**What NOT to mock**:
- SQLite (use real temp database)
- Config loading (use real config files)
- Storage layer (use real models)
- Message/routing logic (test real behavior)

---

## Parametrized Test Ideas

Many tests can use `@pytest.mark.parametrize` to test multiple cases:

```python
@pytest.mark.parametrize("prompt,expected_route", [
    ("What's the weather?", "simple"),
    ("Design a distributed system", "complex"),
    ("Write Python code", "code"),
    ("Create a poem", "creative"),
])
def test_classifier_routes(prompt, expected_route):
    ...
```

This reduces code duplication and makes test intent clearer.

