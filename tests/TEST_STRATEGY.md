# BeigeBox Test Strategy — Definition & Guidelines

This document defines how we create and organize tests going forward. It's our contract with code quality and future maintainers.

---

## Core Philosophy

**"Test what users will actually do, not what implementations happen to do."**

- Tests verify **behavior**, not implementation details
- Minimize mocking; maximize real components
- E2E tests provide confidence; unit tests provide speed
- Error paths matter as much as happy paths
- Tests are documentation for expected behavior

---

## Test Organization

```
tests/
├── TEST_STRATEGY.md           ← This file
├── conftest.py                ← Shared fixtures
├── test_e2e_*.py              ← End-to-end flows (real FastAPI client)
├── test_integration_*.py       ← Component integration (real + mocked boundaries)
├── test_unit_*.py             ← Fast unit tests (heavily mocked)
├── test_error_*.py            ← Error scenarios & recovery
├── test_regression_*.py        ← Bugs we've fixed (prevent reappearing)
└── test_load_*.py             ← Performance & concurrency
```

---

## Test Categories & Guidelines

### 1. E2E Tests (`test_e2e_*.py`)
**Purpose**: Verify complete user workflows work end-to-end

**Rules**:
- Use `TestClient(app)` — real FastAPI routing
- No mocking of endpoints (mock external services only: Ollama, Chroma)
- Test happy path + common errors
- Verify response structure (status, headers, body schema)

**Example**: Operator stream emits correct SSE events

```python
@pytest.mark.asyncio
async def test_operator_stream_returns_valid_sse_events():
    """POST /api/v1/operator/stream returns SSE events with correct structure"""
    # Uses real FastAPI, not mocked
    # Mocks Ollama backend only
    pass
```

---

### 2. Integration Tests (`test_integration_*.py`)
**Purpose**: Verify multiple real components work together

**Rules**:
- Use real implementations (VectorStore, SQLiteStore, ToolRegistry)
- Mock only external services (Ollama, APIs)
- Test data flow between components
- Verify state changes (database writes, file creation)

**Example**: Operator uses real ChromaDB to store/retrieve memory

```python
@pytest.mark.asyncio
async def test_operator_memory_tool_uses_real_vector_store():
    """Operator memory tool queries real ChromaDB, not mock"""
    vs = VectorStore(backend=make_backend("chromadb", path=tmp_db))
    # Real vector store, mocked Ollama backend
    pass
```

---

### 3. Error Scenario Tests (`test_error_*.py`)
**Purpose**: Verify graceful handling of failures

**Rules**:
- Test each error type: timeout, network, validation, unavailable resource
- Verify error event is emitted (not silent failure)
- Verify system recovers (doesn't crash)
- Test retry logic if present

**Error types to cover**:
- Backend unavailable (Ollama down)
- Tool timeout (>30s)
- Malformed input (invalid JSON)
- Rate limit (429)
- Out of memory / resource exhausted

**Example**:

```python
@pytest.mark.asyncio
async def test_operator_handles_backend_timeout():
    """Operator gracefully handles backend timeout"""
    with patch('httpx.AsyncClient.post', side_effect=asyncio.TimeoutError):
        events = await op.run("test", history=[])
        # Should emit error event, not crash
        assert any(e['type'] == 'error' for e in events)
```

---

### 4. Regression Tests (`test_regression_*.py`)
**Purpose**: Prevent bugs from reappearing

**Rules**:
- One test per historical bug
- Test name documents the bug: `test_autonomous_mode_no_infinite_loop`
- Include comment with issue number or bug description
- Keep forever (don't delete old regression tests)

**Example**:

```python
@pytest.mark.asyncio
async def test_autonomous_mode_early_exit_on_no_tool_calls():
    """Regression: Issue #456 — autonomous mode looped forever if first turn had no tool calls"""
    # This test documents the fix and prevents reappearing
    pass
```

---

### 5. Unit Tests (`test_unit_*.py`)
**Purpose**: Fast feedback on individual functions

**Rules**:
- Mock external dependencies (Ollama, HTTP, filesystem)
- Test pure logic: algorithms, calculations, transformations
- Keep tests <50ms each
- Test edge cases and boundary conditions

**When to write**:
- Data transformation logic (token count, chunk size)
- Sorting/filtering algorithms
- Validation rules
- Config parsing

**When NOT to write**:
- Don't test controller logic (test via E2E instead)
- Don't test third-party libraries
- Don't mock everything and test nothing real

---

### 6. Load & Performance Tests (`test_load_*.py`)
**Purpose**: Verify system meets performance SLAs

**Rules**:
- Measure TTFT (time to first token)
- Measure total latency (all tokens)
- Measure throughput (requests/sec)
- Report p50, p95, p99 latencies
- Use pytest-benchmark or custom timer

**Example**:

```python
@pytest.mark.benchmark
def test_operator_ttft_sla(benchmark):
    """Operator TTFT < 2 seconds (p95)"""
    # Benchmark first event latency
    pass
```

---

## Writing a Good Test

### Template

```python
@pytest.mark.asyncio
async def test_<feature>_<behavior>():
    """
    One-line: what user does

    Longer: why this matters, what we're verifying
    """
    # ── Setup ──
    # Create fixtures, mock external services

    # ── Action ──
    # Call the function being tested

    # ── Assert ──
    # Verify correct behavior
    # Verify state changed correctly
```

### Checklist

- [ ] Test name is descriptive (`test_operator_autonomous_loop_exits_early`, not `test_op`)
- [ ] Docstring explains what user would experience
- [ ] Happy path test exists
- [ ] At least one error path test exists
- [ ] Test data is realistic (not just `"test"` or `123`)
- [ ] Test runs in <100ms (or marked as slow)
- [ ] Test is independent (no shared state with other tests)
- [ ] Test verifies behavior, not implementation (no spy on private methods)

---

## Mocking Policy

### ✅ DO Mock:
- External services (Ollama, ChromaDB, APIs)
- HTTP calls
- File I/O (unless testing file operations)
- Time-based operations (clock, delays)
- Environment variables

### ❌ DON'T Mock:
- FastAPI app/client
- ToolRegistry
- Operator (unless testing integration with operator)
- Business logic

**Reasoning**: The more real code runs, the more confident we are. Mock only the boundaries that are expensive or slow.

---

## Running Tests

```bash
# All tests
pytest

# By category
pytest tests/test_e2e_*.py
pytest tests/test_integration_*.py
pytest tests/test_error_*.py
pytest tests/test_regression_*.py
pytest tests/test_unit_*.py

# With coverage report
pytest --cov=beigebox --cov-report=html

# Slow tests only
pytest -m slow --durations=10

# Performance benchmarks
pytest tests/test_load_*.py --benchmark-only

# Fast tests only (unit + fast integration)
pytest tests/test_unit_*.py tests/test_integration_*.py -x
```

---

## Coverage Targets

| Component | Target | Rationale |
|-----------|--------|-----------|
| `proxy.py` | 85%+ | Core request pipeline |
| `operator.py` | 80%+ | Tool dispatch logic |
| `config.py` | 90%+ | Config is simple, should be fully tested |
| `backends/router.py` | 85%+ | Routing logic critical |
| `tools/*` | 70%+ | Tool-specific, some hard to test |

**Total target**: 75%+ overall coverage

---

## CI/CD Integration

When this gets hooked into GitHub Actions:

```yaml
- name: Run fast tests (unit + integration)
  run: pytest tests/test_unit_*.py tests/test_integration_*.py -x

- name: Run E2E tests
  run: pytest tests/test_e2e_*.py --timeout=30

- name: Check coverage
  run: pytest --cov=beigebox --cov-report=term-fail-under=75

- name: Run regression tests (slow, but important)
  run: pytest tests/test_regression_*.py --timeout=60
```

---

## Adding a New Feature

When you add a feature, **write tests in this order**:

1. **E2E test** (what does the user do?)
   - ```python
     def test_user_can_do_X():
         resp = client.post("/api/endpoint", json={...})
         assert resp.status_code == 200
     ```

2. **Happy path integration** (do components work together?)
   - ```python
     def test_operator_calls_tool_correctly():
         pass
     ```

3. **Error scenario** (what if it fails?)
   - ```python
     def test_operator_handles_tool_timeout():
         pass
     ```

4. **Unit tests** (fast feedback on logic)
   - Only if the feature has complex logic

---

## Common Mistakes to Avoid

❌ **Too many mocks**
```python
# DON'T: Tests nothing real
def test_operator():
    op = Operator(vector_store=Mock(), tool_registry=Mock(), ...)
    # Now we're just testing the test setup, not the feature
```

✅ **Real components, mock boundaries**
```python
# DO: Real operator with mocked Ollama
def test_operator():
    op = Operator(vector_store=real_vs)
    with patch('httpx.AsyncClient.post', return_value=...):
        result = op.run("query", history=[])
```

---

❌ **Testing implementation details**
```python
# DON'T: Brittle to refactoring
def test_operator():
    op = Operator()
    assert op._model == "qwen3:4b"  # Implementation detail!
```

✅ **Testing behavior**
```python
# DO: Verifies contract
def test_operator():
    op = Operator()
    events = op.run("query", history=[])
    assert any(e['type'] == 'answer' for e in events)  # Behavior!
```

---

## When to Use Marks

```python
@pytest.mark.asyncio       # Async test (required)
@pytest.mark.slow          # Takes >1 second
@pytest.mark.integration   # Uses real components (informational)
@pytest.mark.e2e           # End-to-end flow (informational)
@pytest.mark.benchmark     # Performance test
@pytest.mark.skip          # Temporarily disable
@pytest.mark.xfail         # Expected to fail (documents known issue)
```

---

## Resources

- TESTING.md — detailed test types & examples
- conftest.py — shared fixtures (read before writing tests)
- pytest docs: https://docs.pytest.org/
- Hypothesis: https://hypothesis.readthedocs.io/

---

**Last updated**: 2026-03-11
**Test suite maturity**: Building Tier 1 → Tier 2 → Tier 3
