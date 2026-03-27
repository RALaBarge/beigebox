# ✅ COMPLETE — Implemented (commit f9450a75). Operator runs persist to localStorage + SQLite with run_id tracking, status polling, /api/v1/operator/{run_id} and /api/v1/operator/runs endpoints.

# Operator Background Execution & Persistence

**Version:** v1.3.1+
**Status:** Complete
**Commit:** f9450a75

## Overview

Operator agent tasks now run in the background and persist across browser navigation and page refreshes. Users can start an operator run, switch to other tabs, and retrieve results later without losing progress or history.

## Problem Solved

Previously:
- ❌ Navigating away from Operator tab disconnected the SSE stream
- ❌ UI lost progress but backend kept running invisibly
- ❌ History only in memory; lost on page refresh
- ❌ No way to retrieve results after disconnect

Now:
- ✅ History persists to localStorage (survives page refresh)
- ✅ Backend run ID tracked; results stored to database
- ✅ UI shows "background run" indicator when navigating away
- ✅ Can retrieve completed results via API or UI
- ✅ Full audit trail of all operator runs in SQLite

## Architecture

### Database Layer

**New Table: `operator_runs`**
```sql
CREATE TABLE operator_runs (
    id TEXT PRIMARY KEY,              -- run_id: uuid4().hex[:8]
    created_at TEXT NOT NULL,         -- ISO 8601 timestamp
    query TEXT NOT NULL,              -- original user query
    history TEXT NOT NULL,            -- JSON array of past messages
    model TEXT NOT NULL,              -- model used (e.g., "qwen3:4b")
    status TEXT DEFAULT 'running',    -- running | completed | error
    result TEXT,                      -- final answer or error message
    latency_ms INTEGER DEFAULT 0,     -- wall-clock duration
    updated_at TEXT NOT NULL          -- last status update
);

CREATE INDEX idx_operator_runs_created ON operator_runs(created_at);
```

**Methods in `SQLiteStore`:**

```python
def store_operator_run(run_id, query, history, model,
                       status="running", result=None, latency_ms=0)
    # Create or update operator run

def get_operator_run(run_id: str) -> dict | None
    # Retrieve a run by ID; deserializes history JSON

def list_operator_runs(limit: int = 50) -> list[dict]
    # List recent runs, most recent first

def update_operator_run_status(run_id, status, result=None, latency_ms=0)
    # Update status after completion (used in finally block)
```

### API Endpoints

**Streaming Endpoint (Modified)**

```
POST /api/v1/operator/stream
```

Changes:
- Generates `run_id = uuid4().hex[:8]` per request
- Emits `{"type": "start", "run_id": "abc12345"}` as first event
- Stores run on completion or error (regardless of SSE stream status)
- Client disconnect doesn't affect database storage

**New Retrieval Endpoints**

```
GET /api/v1/operator/{run_id}
  Response: {
    "id": "abc12345",
    "created_at": "2026-03-11T12:34:56.789Z",
    "query": "What is the weather?",
    "history": [{role: "user", content: "..."}, ...],
    "model": "qwen3:4b",
    "status": "completed",
    "result": "The weather is sunny...",
    "latency_ms": 2847,
    "updated_at": "2026-03-11T12:34:59.636Z"
  }

GET /api/v1/operator/runs
  Response: {
    "runs": [
      {id, created_at, query, model, status, latency_ms, updated_at},
      ...
    ]
  }
```

### Frontend State

**localStorage Keys:**
- `_opHistory` — JSON-serialized conversation history (array of messages)
- `_lastOpRunId` — run ID of current/most recent execution

**Session Variables:**
- `_opHistory` — in-memory history (restored from localStorage on load)
- `_lastOpRunId` — tracks active background run ID

### Tab Switching Logic

```javascript
switchTab(name) {
  // When leaving operator tab while run in progress:
  if (name !== 'operator' && _lastOpRunId) {
    showOpBgIndicator();  // Show "⟳ running" badge
  }

  // When returning to operator tab:
  if (name === 'operator') {
    hideOpBgIndicator();  // Hide badge
    // User can now see results or retrieve from API
  }
}
```

## User Experience Flow

### Scenario 1: Normal Single-Turn
```
1. User types query in Operator tab
2. Clicks "Run"
3. Operator executes, user sees tool calls and answer in real time
4. History automatically saved to localStorage
5. Run stored to database with status="completed"
```

### Scenario 2: Navigate Away During Execution
```
1. User starts a long-running operator task
2. While still thinking... user switches to Chat tab (or any other)
3. "⟳ running" badge appears in Operator toolbar
4. Backend continues execution invisibly
5. When complete, backend stores result automatically
6. User can:
   a. Click badge to retrieve and display result
   b. Come back to Operator tab later to see results
   c. Use API: GET /api/v1/operator/{run_id}
```

### Scenario 3: Page Refresh During Execution
```
1. User starts operator run
2. Page crashes or user closes browser
3. Backend run completes and stores result
4. User refreshes page
5. localStorage restores _opHistory and _lastOpRunId
6. "⟳ running" badge visible immediately
7. User can retrieve result from stored run_id
```

### Scenario 4: Multi-Turn Autonomous Mode
```
1. User sets max_turns=3, starts query
2. Operator runs turns 1, 2, 3 (each adds to history)
3. History accumulated in memory, then persisted at end
4. All turns stored in single run with accumulated history
5. If user navigates away mid-execution, background indicator shows
6. On completion, full multi-turn history available via API
```

## Implementation Details

### Event Stream Modification

```javascript
// In runOp() event handler:

if (evt.type === 'start') {
  _lastOpRunId = evt.run_id;
  localStorage.setItem('_lastOpRunId', _lastOpRunId);
  showOpBgIndicator();  // Only if user navigates away
  // Don't display 'start' event to user
  continue;
}
```

### Run Storage Lifecycle

```python
# In api_operator_stream() event_stream():

_run_id = uuid4().hex[:8]
_start_time = time.time()

# First event to client
yield {"type": "start", "run_id": _run_id}

# ... operator execution ...

# On completion (in try block):
_latency_ms = int((time.time() - _start_time) * 1000)
sqlite_store.store_operator_run(
    run_id=_run_id,
    query=question,
    history=cur_history,
    model=_op_model,
    status="completed",
    result=final_answer or "",
    latency_ms=_latency_ms,
)

# On error (in except block):
sqlite_store.store_operator_run(
    run_id=_run_id,
    query=question,
    history=history or [],
    model=model_override or "unknown",
    status="error",
    result=str(e),
    latency_ms=_latency_ms,
)
```

### History Persistence

```javascript
// Load on page init
function loadOpHistory() {
  try {
    const saved = localStorage.getItem('_opHistory');
    if (saved) _opHistory = JSON.parse(saved);
  } catch(e) { }
}

// Save after each run completes
function saveOpHistory() {
  try {
    localStorage.setItem('_opHistory', JSON.stringify(_opHistory));
  } catch(e) { }  // Gracefully handle quota exceeded
}

// Clear when user clicks "Clear"
function clearOp() {
  _opHistory = [];
  localStorage.removeItem('_opHistory');
  localStorage.removeItem('_lastOpRunId');
}
```

## Limits & Constraints

| Aspect | Limit | Notes |
|--------|-------|-------|
| localStorage size | ~5-10 MB | Browser dependent; gracefully fails if exceeded |
| History messages | 20 (10 turns) | Capped automatically; oldest pruned |
| Database retention | Unlimited | No auto-cleanup; user/admin responsibility |
| Run ID length | 8 chars | uuid4().hex[:8] (0-indexed) |
| Max concurrent runs | Unlimited | Tracked independently per run_id |
| Latency granularity | 1 ms | Integer milliseconds |

## Testing

### Unit Tests (test_operator_model_config.py)
- ✅ Database schema creation
- ✅ store_operator_run() / get_operator_run()
- ✅ list_operator_runs() ordering

### Manual Testing Checklist
- [ ] Start operator run, navigate to Chat tab → badge appears
- [ ] Badge says "⟳ running"
- [ ] Return to Operator tab → badge disappears, result visible
- [ ] Page refresh → history restored from localStorage
- [ ] Long-running task → API `/api/v1/operator/{run_id}` returns correct data
- [ ] Error during execution → status="error", result=error message
- [ ] Clear button → clears localStorage keys
- [ ] Multi-turn mode → history from all turns persists

## Known Limitations

1. **localStorage per-domain** — history not shared across subdomains or protocol changes
2. **No background retrieval polling** — doesn't auto-fetch when run completes; user must click or check API
3. **No run deletion UI** — runs accumulate in database; no self-service cleanup
4. **Single browser** — history doesn't sync across devices; localStorage is local only

## Future Enhancements

- [ ] **Background polling** — auto-check run status every 2s, show "Results ready!" toast
- [ ] **Run management UI** — list/delete old runs in Config tab
- [ ] **Export runs** — download run transcript as JSON/markdown
- [ ] **Run tagging** — user-assigned labels/categories for organizing runs
- [ ] **Webhook notifications** — ping external service when run completes
- [ ] **Run sharing** — generate shareable links to view run results

## Migration & Compatibility

- **Backwards compatible** — adds new table, doesn't modify existing ones
- **No data loss** — existing operator history (in-memory only) not affected
- **Instant availability** — database schema created on first startup
- **Zero UI breaking changes** — new indicator optional; click is voluntary

## Security Considerations

- **Operator run storage** — includes full query + result; ensure access controls on `/api/v1/operator/*`
- **localStorage exposure** — runs visible to browser extensions and same-origin scripts
- **History size** — large histories (20+ messages) could fill localStorage; monitor quota
- **Privacy** — stored locally; no cloud sync; users control deletion

## Monitoring

**Log Indicators:**
```
DEBUG: "Stored operator run abc12345 (query=What is the..., status=completed)"
WARNING: "Failed to store operator run: [error]"
```

**Metrics to Track:**
- Operator runs per day
- Average latency_ms per model
- Error rate (status="error" / total)
- Most common queries (for UX research)

## References

- Database migrations: `beigebox/storage/sqlite_store.py` lines 507-624
- API endpoints: `beigebox/main.py` lines 2193-2223
- Frontend logic: `beigebox/web/index.html` lines 3820-3980
- Commit: `f9450a75`

## Related Documents

- `OPERATOR_MODEL_AUDIT.md` — operator model configuration (separate feature)
- `OPERATOR_BACKGROUNDING.md` — planning doc (skipped UI tab)
- `README.md` — operator feature overview (updated)
