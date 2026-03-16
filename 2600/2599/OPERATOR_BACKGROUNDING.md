# Operator Background Execution & Persistence

## Current Behavior
- ✅ Operator continues running on the backend even if you navigate away
- ❌ Frontend loses the SSE stream and stops showing progress
- ❌ History only persists in memory (`_opHistory` array)
- ❌ No way to retrieve results after disconnecting

## Two Solutions

### Option 1: Client-Side Only (Quick, No Backend Changes)
**Pros:** No server changes needed, works immediately
**Cons:** Limited to current browser/device, history lost on browser clear

#### Changes:
1. **Persist history to localStorage:**
   ```javascript
   // In web/index.html runOp()
   function saveOpHistory() {
     localStorage.setItem('_opHistory', JSON.stringify(_opHistory));
   }

   function loadOpHistory() {
     const saved = localStorage.getItem('_opHistory');
     return saved ? JSON.parse(saved) : [];
   }

   // On page load:
   _opHistory = loadOpHistory();

   // After each operator run:
   _opHistory.push({role: 'user', content: query});
   _opHistory.push({role: 'assistant', content: finalAnswer});
   saveOpHistory();
   ```

2. **Add "Background Runs" panel:**
   - Show a list of runs with status (running, completed, error)
   - Use `setInterval` to poll `/api/v1/operator/stream` status
   - Actually: can't poll SSE easily; need run ID tracking on backend

### Option 2: Backend Run Tracking (Recommended)
**Pros:** Full persistence, works across devices, can resume runs
**Cons:** Requires ~200 lines backend code + UI updates

#### Backend Changes Needed:

1. **Add operator run storage to SQLiteStore:**
   ```python
   # beigebox/storage/sqlite_store.py
   def store_operator_run(self, run_id: str, query: str, history: list,
                         model: str, status: str, result: str = None):
       """Store operator run with full context."""

   def get_operator_run(self, run_id: str):
       """Retrieve a stored operator run."""

   def list_operator_runs(self, limit: int = 50):
       """List all operator runs (most recent first)."""
   ```

2. **Modify `/api/v1/operator/stream` endpoint:**
   ```python
   # Generate run_id at start
   run_id = str(uuid4())[:8]

   # Emit run_id in first event
   yield f"data: {json.dumps({'type': 'start', 'run_id': run_id})}\n\n"

   # Store run after completion
   store.store_operator_run(
       run_id=run_id,
       query=query,
       history=history,
       model=op._model,
       status='completed',
       result=final_answer
   )
   ```

3. **Add retrieval endpoints:**
   ```python
   @app.get("/api/v1/operator/{run_id}")
   async def get_operator_run(run_id: str):
       """Get results of a completed operator run."""
       run = store.get_operator_run(run_id)
       if not run:
           return JSONResponse({"error": "Run not found"}, status_code=404)
       return JSONResponse(run)

   @app.get("/api/v1/operator/runs")
   async def list_operator_runs():
       """List all operator runs."""
       return JSONResponse(store.list_operator_runs())
   ```

#### Frontend Changes:

1. **Capture run_id from first event:**
   ```javascript
   let currentRunId = null;
   const reader = response.body.getReader();
   // When 'start' event arrives:
   currentRunId = event.run_id;
   localStorage.setItem('_lastOpRunId', currentRunId);
   ```

2. **Add "Active Runs" panel:**
   ```javascript
   // Show list of background operator runs
   async function loadBackgroundRuns() {
     const resp = await fetch('/api/v1/operator/runs');
     const runs = await resp.json();
     // Filter for incomplete/recent runs
     // Show in side panel with retrieve button
   }

   async function retrieveRunResult(runId) {
     const resp = await fetch(`/api/v1/operator/${runId}`);
     const run = await resp.json();
     // Display result and add to history
     _opHistory = run.history;
     displayOpResult(run.result);
   }
   ```

3. **Auto-restore on page load:**
   ```javascript
   // Check if there's a background run in progress
   const lastRunId = localStorage.getItem('_lastOpRunId');
   if (lastRunId) {
     // Poll for completion
     const checkInterval = setInterval(async () => {
       const run = await fetch(`/api/v1/operator/${lastRunId}`).then(r => r.json());
       if (run.status === 'completed') {
         clearInterval(checkInterval);
         // Restore history and result
         retrieveRunResult(lastRunId);
       }
     }, 2000);  // Poll every 2s
   }
   ```

## Recommended Approach

**Hybrid (Option 1 + minimal backend):**
1. Add localStorage persistence immediately (1 hour work)
2. Add run_id generation to `/api/v1/operator/stream` (30 mins)
3. Add `/api/v1/operator/{run_id}` retrieval endpoint (30 mins)
4. Add "Operator Runs" history tab in UI (2 hours)

This gives you:
- ✅ History persists across page refreshes
- ✅ Can retrieve results after SSE disconnects
- ✅ Can view past operator runs
- ✅ Zero breaking changes

## Files to Modify

### Backend
- `beigebox/main.py` — add run_id tracking to operator/stream endpoint
- `beigebox/storage/sqlite_store.py` — add operator_run table + queries
- `cli.py` — optionally add `beigebox operator-runs` command

### Frontend
- `beigebox/web/index.html` — localStorage + run ID handling + history UI
- Optionally add new "Operator Runs" tab to show history

## Test Cases Covered

```python
# test_operator_backgrounding.py
- Operator run generates unique run_id
- Run result retrievable after SSE disconnect
- History persists in localStorage
- Can query past operator runs
- Can resume/retry past runs
```
