# BeigeBox Testing Strategy — Addressing HN Critics

This document outlines the test types that Hacker News critics consistently demand. Each category includes reasoning and example implementations.

---

## 1. End-to-End (E2E) Tests
**HN criticism**: "No tests verify the actual request → response flow"

### What to test:
- Real HTTP requests to FastAPI endpoints (not mocked)
- Full request parsing → routing → backend call → response streaming
- SSE event correctness (turn_start, tool_call, answer, turn_complete)
- Status codes, response headers, content-type

### Example:
```python
@pytest.mark.asyncio
async def test_operator_stream_e2e():
    """Real HTTP request through operator/stream endpoint"""
    client = TestClient(app)
    resp = client.post("/api/v1/operator/stream",
        json={"query": "what is 2+2?", "history": []})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/event-stream"

    # Parse SSE events from response
    events = [json.loads(line[6:]) for line in resp.text.split('\n') if line.startswith('data: ')]
    assert any(e['type'] == 'answer' for e in events)
```

---

## 2. Integration Tests
**HN criticism**: "Components are tested in isolation; real interactions aren't verified"

### What to test:
- Operator + VectorStore (embedding lookup)
- Operator + Tool Registry (tool dispatch)
- Proxy + Decision Agent + Backend Router (full pipeline)
- Cache layer + storage layer (cache misses/hits)

### Example:
```python
@pytest.mark.asyncio
async def test_operator_with_real_vector_store():
    """Operator uses real ChromaDB, not mocked storage"""
    vs = VectorStore(backend=make_backend("chromadb", path=tmp_path))
    op = Operator(vector_store=vs)

    events = await op.run(
        "Recall: I told you my name is Alice. What's my name?",
        history=[]
    )

    # Verify memory tool was called, vector_store was queried
    assert any(e['tool'] == 'memory' for e in events if e['type'] == 'tool_call')
```

---

## 3. Data Flow Tests
**HN criticism**: "No tests verify data actually moves through the system correctly"

### What to test:
- User input → tool input (parameter transformation)
- Tool output → context injection (truncation, embedding)
- Context → LLM prompt (no data loss)
- Streaming → reassembly (buffering correctness)

### Example:
```python
@pytest.mark.asyncio
async def test_operator_tool_output_flows_to_next_message():
    """Tool result is injected into context for next LLM call"""
    op = Operator(vector_store=mock_vs)

    # Track what gets sent to backend
    sent_to_backend = []

    with patch.object(op, '_call_backend') as mock_backend:
        mock_backend.side_effect = lambda msgs, **kw: (
            sent_to_backend.append(msgs),
            [{"role": "assistant", "content": "ok"}]
        )[1]

        events = await op.run("search for python docs", history=[])

    # Verify tool result from first call is in messages for second call
    all_messages = [m for msgs in sent_to_backend for m in msgs]
    tool_results = [m for m in all_messages if 'tool_result' in m.get('content', '')]
    assert len(tool_results) > 0
```

---

## 4. Error Scenario Tests
**HN criticism**: "Only happy path works. What happens when things fail?"

### What to test:
- Backend timeout → graceful error event
- Tool failure → operator recovery + retry or abort
- Network error → circuit breaker behavior
- Malformed response → validation error
- Model unavailable → fallback or informative error

### Example:
```python
@pytest.mark.asyncio
async def test_operator_handles_tool_timeout():
    """Operator gracefully handles tool that times out"""
    op = Operator()

    with patch('beigebox.tools.registry.ToolRegistry.execute') as mock_exec:
        mock_exec.side_effect = asyncio.TimeoutError("tool took >30s")

        events = await op.run("run slow_tool()", history=[])

        # Verify error event emitted, not crashed
        assert any(e['type'] == 'error' for e in events)
        assert any('timeout' in e.get('message', '').lower() for e in events if e['type'] == 'error')
```

---

## 5. Concurrency / Race Condition Tests
**HN criticism**: "No tests for concurrent requests or race conditions"

### What to test:
- Two users querying operator simultaneously (shared state safe?)
- Operator + semantic cache (concurrent reads/writes)
- Tool registry (thread-safe registration)
- Storage writes (no corruption under load)

### Example:
```python
@pytest.mark.asyncio
async def test_concurrent_operator_runs():
    """Two operator requests run safely in parallel"""
    op1 = Operator(vector_store=shared_vs)
    op2 = Operator(vector_store=shared_vs)

    tasks = [
        op1.run("write file A", history=[]),
        op2.run("write file B", history=[]),
    ]

    results = await asyncio.gather(*tasks)

    # Both completed without errors
    assert all(any(e['type'] == 'answer' for e in r) for r in results)
    # Files don't overwrite each other
    assert workspace_out/"A.txt" exists and workspace_out/"B.txt" exists
```

---

## 6. State Verification Tests
**HN criticism**: "Tests don't verify the system actually changed state"

### What to test:
- After operator runs: conversation saved to SQLite
- After tool call: workspace file actually created
- After semantic cache store: embedding searchable
- After config reload: new setting takes effect

### Example:
```python
@pytest.mark.asyncio
async def test_operator_run_persists_to_sqlite():
    """Operator run is stored in conversation history"""
    sqlite = SQLiteStore(":memory:")
    op = Operator(vector_store=vs)

    await op.run("calculate 5+5", history=[])

    # Query database directly
    convs = sqlite.get_conversations()
    messages = sqlite.get_messages(convs[0].id)

    # Verify: user message, tool calls, assistant answer all persisted
    assert any(m.role == 'user' and '5+5' in m.content for m in messages)
    assert any(m.role == 'assistant' for m in messages)
```

---

## 7. Property-Based Tests
**HN criticism**: "No invariants tested; what if I feed weird inputs?"

### What to test:
- SSE events always have required fields (type, content/message)
- History can handle arbitrary message lengths
- Token counts are always >= actual content length
- Operators never hallucinate extra tool calls

### Example:
```python
from hypothesis import given, strategies as st

@given(
    query=st.text(min_size=1, max_size=10000),
    history=st.lists(
        st.fixed_dictionaries({"role": st.just("user"), "content": st.text()}),
        max_size=50
    )
)
@pytest.mark.asyncio
async def test_operator_handles_any_input(query, history):
    """Operator should never crash on any text input"""
    op = Operator()
    try:
        events = await op.run(query, history=history)
        # Should always produce at least an answer or error
        assert any(e['type'] in ['answer', 'error'] for e in events)
    except Exception as e:
        # If it fails, must be a known error type
        assert isinstance(e, (ValueError, TimeoutError, asyncio.CancelledError))
```

---

## 8. Performance / Load Tests
**HN criticism**: "No benchmarks; does it scale? How slow is it?"

### What to test:
- Single operator run latency (TTFT, total time)
- Throughput: how many concurrent requests can it handle?
- Cache hit rate (semantic cache effectiveness)
- Token/second throughput
- Memory usage over 1000 requests

### Example:
```python
@pytest.mark.benchmark
def test_operator_latency(benchmark):
    """Operator responds within acceptable latency"""
    op = Operator()

    def run():
        return asyncio.run(op.run("what is 2+2?", history=[]))

    result = benchmark(run)

    # TTFT: first event within 1 second
    # Total: all events within 10 seconds
    assert result.stats.median < 10.0
```

---

## 9. Contract Tests (API Boundaries)
**HN criticism**: "Frontend and backend might disagree on API shape"

### What to test:
- Request body schema matches what backend expects
- SSE event schema matches what frontend parses
- Error response format is consistent
- Model list endpoint returns what panes expect

### Example:
```python
def test_operator_stream_request_schema():
    """Request body matches OpenAPI spec"""
    schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "history": {"type": "array"},
            "model": {"type": "string"},
            "max_turns": {"type": "integer", "minimum": 1, "maximum": 20},
        }
    }

    # Valid requests
    assert validate_schema({"query": "hi"}, schema)
    assert validate_schema({"query": "hi", "max_turns": 5}, schema)

    # Invalid requests
    assert not validate_schema({"max_turns": 5}, schema)  # missing query
    assert not validate_schema({"query": "hi", "max_turns": 0}, schema)  # min 1
```

---

## 10. Regression Tests
**HN criticism**: "No tests for previous bugs; same bugs reappear"

### What to test:
- Autonomous mode: no tool calls → early exit (not infinite loop)
- History threading: turn N receives turn N-1 answer in context
- Turn separators: render at correct positions
- Wiretap: logs each turn separately (not collapsed)

### Example:
```python
@pytest.mark.asyncio
async def test_autonomous_mode_no_infinite_loop():
    """Regression: autonomous mode used to loop forever on no tool calls"""
    op = Operator()  # Will immediately answer without tools

    events = await op.run("what color is the sky?", history=[])

    # Should not emit turn_start for turn 2+ if turn 1 had no tool calls
    turn_starts = [e for e in events if e['type'] == 'turn_start']
    assert len(turn_starts) == 0
```

---

## 11. Config / Feature Flag Tests
**HN criticism**: "Feature flags are untested; code paths diverge"

### What to test:
- autonomous.enabled=false → single turn (no turn separators)
- autonomous.enabled=true, max_turns=3 → max 3 turns
- max_turns override in request → overrides config
- Runtime config reload (hot-reload actually works)

### Example:
```python
@pytest.mark.asyncio
async def test_autonomous_mode_respects_config():
    """autonomous.enabled controls whether multi-turn is available"""

    # Config: disabled
    with patch('beigebox.config.get_config') as mock_cfg:
        mock_cfg.return_value = {"operator": {"autonomous": {"enabled": False, "max_turns": 5}}}
        max_turns = read_config()["operator"]["autonomous"]["max_turns"]

        # But single-turn request still works
        assert max_turns == 5  # config value
        # Endpoint should enforce max_turns=1 unless request overrides
```

---

## 12. Documentation / Examples Tests
**HN criticism**: "Docs are out of date with code; examples don't work"

### What to test:
- README examples actually execute
- Config examples are valid YAML
- API examples in comments match implementation
- Docstrings match actual behavior

### Example:
```python
def test_config_example_is_valid():
    """Config examples in docstrings are valid YAML"""
    import yaml
    config_yaml = """
operator:
  autonomous:
    enabled: false
    max_turns: 5
"""
    cfg = yaml.safe_load(config_yaml)
    assert cfg["operator"]["autonomous"]["max_turns"] == 5
```

---

## Implementation Priority

**Tier 1 (Must Have — blocks releases)**
1. E2E: operator/stream produces correct SSE events
2. Integration: operator + vector_store + tool_registry
3. Error scenarios: tool timeout, backend unavailable
4. Regression: autonomous mode early exit

**Tier 2 (Should Have — production quality)**
5. Data flow: tool output → context → LLM
6. State verification: SQLite persistence
7. Contract tests: request/response schema
8. Config tests: feature flags work as documented

**Tier 3 (Nice to Have — maturity)**
9. Concurrency: parallel requests don't corrupt state
10. Property-based: handle weird inputs
11. Performance benchmarks: latency SLA
12. Hot-reload: runtime config changes take effect

---

## Running Tests

```bash
# All tests
pytest

# By category
pytest -m "e2e"
pytest -m "integration"
pytest -m "error_scenario"
pytest -m "regression"

# With coverage
pytest --cov=beigebox --cov-report=html

# Performance benchmarks
pytest tests/ -k benchmark --benchmark-only

# Load test
pytest tests/test_load.py -n 50 --durations=10
```

---

## Links
- [Hypothesis for property-based testing](https://hypothesis.readthedocs.io/)
- [pytest-benchmark](https://pytest-benchmark.readthedocs.io/)
- [Consumer-Driven Contract Testing](https://pact.foundation/)
- [Chaos engineering](https://principlesofchaos.org/)
