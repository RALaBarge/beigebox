# Harness System Logging & Event Format Analysis

**Date**: February 21, 2026  
**Version**: v0.9.2  
**Status**: Current Implementation Review

---

## Executive Summary

The **Harness Orchestrator** currently:

✅ **Streams live events** via Server-Sent Events (SSE) to the client  
✅ **Generates structured event objects** with timestamps and metadata  
✅ **Logs via Python logging** at WARNING level for JSON parse failures  
❌ **Does NOT persist events to wiretap** (no JSONL logging to wire.jsonl)  
❌ **Does NOT store orchestrator runs** to SQLite for replay/analysis  

**Logging happens in two places:**
1. **In-memory event stream** → SSE to web UI (real-time, ephemeral)
2. **Python logger** → Application logs (sparse, errors only)

---

## Event Stream Format

### Location
- **Endpoint**: `POST /api/v1/harness/orchestrate`
- **Implementation**: `beigebox/agents/harness_orchestrator.py` lines 40-148
- **Output**: Server-Sent Events (text/event-stream)

### Event Structure

All events are timestamped dicts, formatted as SSE with newline-delimited JSON:

```
data: {"type": "start", "ts": 1708534800123, "goal": "...", "model": "...", "targets": [...]}

data: {"type": "plan", "ts": 1708534801245, "round": 1, "reasoning": "...", "tasks": [...]}

data: {"type": "dispatch", "ts": 1708534801340, "round": 1, "task_count": 3}

data: {"type": "result", "ts": 1708534805123, "round": 1, "target": "llama3.2:3b", "content": "...", "latency_ms": 3780, "status": "ok"}

data: {"type": "evaluate", "ts": 1708534805456, "round": 1, "assessment": "...", "action": "continue", "rationale": "..."}

data: {"type": "finish", "ts": 1708534806789, "answer": "...", "rounds": 2, "capped": false}

data: [DONE]
```

### Event Types

| Type | Round | Content | Source |
|------|-------|---------|--------|
| `start` | - | Goal, model, available targets | `run()` line 90 |
| `plan` | N | LLM-generated task breakdown | `_plan()` → line 111 |
| `dispatch` | N | Task count for this round | Line 118 |
| `result` | N | Single worker output + latency | `_dispatch()` → line 123 |
| `evaluate` | N | Master assessment + decision | `_evaluate()` → line 136 |
| `finish` | N | Final answer, total rounds, capped? | Lines 105, 140, 148 |
| `error` | N | Exception message | Lines 99, 130 |

### Event Data Fields

#### `_ev()` Helper (line 40)
```python
def _ev(type_: str, **kw) -> dict:
    return {"type": type_, "ts": round(time.monotonic() * 1000), **kw}
```

**Always present**: `type`, `ts` (milliseconds via monotonic clock)  
**Optional**: Round-specific fields added as kwargs

#### Per-event breakdown:

**start**
```json
{
  "type": "start",
  "ts": 1708534800123,
  "goal": "Write and critique a haiku about latency",
  "model": "llama3.2:3b",
  "targets": ["operator", "model:llama3.2:3b"]
}
```

**plan**
```json
{
  "type": "plan",
  "ts": 1708534801245,
  "round": 1,
  "reasoning": "The user wants both a haiku and criticism. I'll delegate...",
  "tasks": [
    {
      "target": "llama3.2:3b",
      "prompt": "Write a haiku about latency",
      "rationale": "Fast model, good at poetry"
    },
    {
      "target": "operator",
      "prompt": "Critique this haiku: [result from task 1]",
      "rationale": "Operator has access to web resources for context"
    }
  ]
}
```

**dispatch**
```json
{
  "type": "dispatch",
  "ts": 1708534801340,
  "round": 1,
  "task_count": 2
}
```

**result**
```json
{
  "type": "result",
  "ts": 1708534805123,
  "round": 1,
  "target": "llama3.2:3b",
  "prompt": "Write a haiku about latency",
  "content": "Ping echoes waiting,\nBytes traverse the digital,\nTimeout arrives.",
  "latency_ms": 3780,
  "status": "ok"
}
```

**evaluate**
```json
{
  "type": "evaluate",
  "ts": 1708534805456,
  "round": 1,
  "assessment": "Got the haiku. Need the critique before synthesizing.",
  "action": "continue",
  "rationale": "Critiquing task not yet complete, waiting for result."
}
```

**finish**
```json
{
  "type": "finish",
  "ts": 1708534806789,
  "answer": "Haiku:\nPing echoes waiting,\nBytes traverse the digital,\nTimeout arrives.\n\nCritique: Clean imagery, strong compression.",
  "rounds": 2,
  "capped": false
}
```

**error**
```json
{
  "type": "error",
  "ts": 1708534806900,
  "message": "Planning failed: Model returned invalid JSON"
}
```

---

## Current Logging Behavior

### Python Application Logger

**File**: `beigebox/agents/harness_orchestrator.py:31`

```python
logger = logging.getLogger(__name__)
```

**Usage** (rare):
```python
# Line 367 — JSON parse failure warning
logger.warning(
    "HarnessOrchestrator: could not parse JSON from LLM output: %s",
    raw[:200]
)
```

**Behavior**: Only logs when LLM returns malformed JSON.

### Web UI Consumption

Events are streamed directly to the browser as Server-Sent Events (SSE):

```javascript
// In web/index.html harness handler
const eventSource = new EventSource("/api/v1/harness/orchestrate", {
  method: "POST",
  body: JSON.stringify({query, targets, model, max_rounds})
});

eventSource.onmessage = (evt) => {
  const event = JSON.parse(evt.data);
  // UI renders event.type-specific pane updates
};
```

**Result**: Events appear live in browser but are **not persisted**.

---

## What's NOT Being Logged

### ❌ Persistent Storage (SQLite)
- Harness runs are not stored to `conversations` table
- No ability to replay a harness orchestration later
- No metrics (which models were called, total cost, latency distribution)

### ❌ Wire Tap (JSONL)
- Harness events don't go to `wire.jsonl`
- Can't audit or inspect via `beigebox tap` or Tap tab
- Missing from conversation replay context

### ❌ Cost Tracking
- Worker latencies are recorded in events but not indexed
- OpenRouter calls within harness workers aren't cost-tracked
- No per-worker cost breakdown in orchestrator results

### ❌ Flight Recorder
- Harness runs don't have per-stage timing recorded
- Can't see wall-clock latency vs task parallelism efficiency
- Missing from flight recorder dashboard

---

## Proposed Enhancement: Harness Event Logging

### Phase 1: Wire Tap Integration

Add harness events to the persistent wiretap log.

**New role**: `"harness"` in wiretap

```python
# In main.py api_harness_orchestrate()
async def _event_stream():
    harness_id = uuid4().hex[:16]
    try:
        async for event in orch.run(goal):
            # Log to wiretap
            if event["type"] in ["start", "plan", "dispatch", "result", "evaluate", "finish"]:
                proxy.wire_log.log(
                    direction="internal",
                    role="harness",
                    content=json.dumps(event),
                    conversation_id=harness_id,
                    model=event.get("model", ""),
                )
            yield f"data: {_json.dumps(event)}\n\n"
    except Exception as e:
        yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"
    yield "data: [DONE]\n\n"
```

**Wiretap output**:
```jsonl
{"ts": "2026-02-21T18:30:00.123456+00:00", "dir": "internal", "role": "harness", "model": "llama3.2:3b", "conv": "a1b2c3d4e5f6g7h8", "len": 287, "content": "{\"type\":\"start\",\"ts\":1708534800123,...}"}
{"ts": "2026-02-21T18:30:01.245678+00:00", "dir": "internal", "role": "harness", "model": "llama3.2:3b", "conv": "a1b2c3d4e5f6g7h8", "len": 456, "content": "{\"type\":\"plan\",\"round\":1,...}"}
```

### Phase 2: SQLite Storage

Add `harness_runs` table to store orchestrator sessions.

```python
# In storage/models.py
class HarnessRun(Base):
    __tablename__ = "harness_runs"
    
    id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Input
    goal = Column(String)
    targets = Column(JSON)
    model = Column(String)
    max_rounds = Column(Integer)
    
    # Output
    final_answer = Column(String)
    total_rounds = Column(Integer)
    was_capped = Column(Boolean)
    
    # Metadata
    total_latency_ms = Column(Integer)
    events_count = Column(Integer)
    events_jsonl = Column(String)  # Full JSONL history
```

**Insertion in endpoint**:
```python
run_id = uuid4().hex
run_record = HarnessRun(
    id=run_id,
    goal=goal,
    targets=targets,
    model=model_override or orch.model,
    max_rounds=max_rounds,
    events_jsonl="",  # Accumulate as stream
)
sqlite_store.session.add(run_record)
sqlite_store.session.commit()

# In event_stream, accumulate:
events_buffer = []
async for event in orch.run(goal):
    events_buffer.append(json.dumps(event) + "\n")
    if event["type"] == "finish":
        run_record.final_answer = event.get("answer")
        run_record.total_rounds = event.get("rounds")
        run_record.was_capped = event.get("capped", False)
        run_record.events_jsonl = "".join(events_buffer)
        sqlite_store.session.commit()
```

### Phase 3: Cost & Performance Tracking

Enhance cost_tracker and flight_recorder for harness context.

```python
# In costs.py — track worker costs
def log_harness_result(
    harness_run_id: str,
    round_num: int,
    target: str,
    latency_ms: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
):
    # Index by harness_run_id for breakdown queries
    pass
```

**Dashboard enhancement**:
- "Harness Runs" card: count, avg latency, last 5 runs
- "Harness Details" modal: per-round breakdown, per-worker cost, efficiency metrics

---

## Recommended Logging Configuration

Add to `config.yaml`:

```yaml
harness:
  enabled: false
  log_to_wiretap: true          # Include harness events in wire.jsonl
  persist_runs: true             # Store runs to SQLite
  retention_days: 90             # Auto-delete old runs
  max_runs_stored: 1000          # Cap total runs in DB
  include_events_in_storage: true # Store full event stream with run
```

**Runtime config** (`runtime_config.yaml`):
```yaml
harness_enabled: false
harness_log_to_wiretap: true
harness_persist_runs: true
```

---

## Testing Harness Logging

```python
# tests/test_harness_logging.py

async def test_harness_events_streamed():
    """Harness returns SSE events correctly."""
    client = TestClient(app)
    
    response = client.post(
        "/api/v1/harness/orchestrate",
        json={
            "query": "Write a haiku",
            "targets": ["llama3.2:3b"],
        },
        stream=True,
    )
    
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            event = json.loads(line[6:])
            if event != "[DONE]":
                events.append(event)
    
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "finish"
    assert all(e.get("ts") for e in events)

def test_harness_logged_to_wiretap():
    """Harness events written to wire.jsonl."""
    # Simulate orchestrator run
    # Check wire.jsonl contains harness role entries
    pass

def test_harness_run_stored_in_sqlite():
    """Orchestrator run persisted to harness_runs table."""
    # Query HarnessRun table for run_id
    # Verify events_jsonl populated
    pass
```

---

## Summary Table

| Aspect | Current | Proposed |
|--------|---------|----------|
| **Stream to UI** | ✅ SSE events | ✅ Keep as-is |
| **Wire log** | ❌ No | ✅ Phase 1 |
| **SQLite storage** | ❌ No | ✅ Phase 2 |
| **Cost tracking** | ❌ No | ✅ Phase 3 |
| **Flight recorder** | ❌ No | ✅ Phase 3 |
| **Replay capability** | ❌ No | ✅ Phase 2 |
| **Config flag** | ❌ No | ✅ harness.persist_runs |

---

## Implementation Priority

**High**: Wire tap + Phase 1 (visibility into orchestrator decisions)  
**Medium**: SQLite + Phase 2 (audit trail, replay)  
**Low**: Cost tracking Phase 3 (requires OpenRouter cost API integration)

---

## References

- Harness orchestrator: `beigebox/agents/harness_orchestrator.py`
- Wiretap format: `beigebox/wiretap.py:53-102`
- Main endpoint: `beigebox/main.py:911-975`
- Web UI consumer: `beigebox/web/index.html` (Harness tab)
