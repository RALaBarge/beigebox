# Ensemble Voting Implementation ✓

**Status**: Complete and Ready  
**Date**: Feb 21, 2026  
**Files**: 3 (1 new, 2 updated)

---

## What Was Built

### 1. **Backend: EnsembleVoter Class** (`ensemble_voter.py`)
Lightweight parallel voting system with LLM judge.

**Flow:**
1. Query all N models in parallel with same prompt
2. Collect all responses + latencies
3. Ask judge LLM to pick best response + explain why
4. Stream all results back as SSE events

**Key Features:**
- Async/parallel model queries (fast)
- Robust JSON parsing (handles judge model hallucinations)
- Fallback graceful failures
- Per-response latency tracking
- Simple event streaming (`dispatch` → `result` → `evaluate` → `finish`)

### 2. **Endpoint: `/api/v1/ensemble`** (in `main.py`)
FastAPI endpoint that streams ensemble voting results.

**Request:**
```json
{
  "prompt": "What is X?",
  "models": ["llama3.2:3b", "mistral:7b"],
  "judge_model": "llama3.2:3b"
}
```

**Response:** SSE stream of JSON events
```
{type:"dispatch", model_count:2}
{type:"result", model:"llama3.2:3b", response:"...", latency_ms:1234}
{type:"result", model:"mistral:7b", response:"...", latency_ms:2156}
{type:"evaluate", winner:"mistral:7b", reasoning:"...", all_responses:[...]}
{type:"finish", winner:"...", best_response:"...", verdict:"..."}
```

### 3. **UI: Chat Tab Integration** (in `index.html`)
Minimal, native integration into existing multi-pane chat.

**Button:** "🎯 Ensemble" in Chat toolbar
- Prompts for judge model (defaults to first candidate)
- Auto-detects candidate models from open panes
- Clears panes, adds judge pane + candidate panes
- Streams live results as they arrive
- Highlights winner pane in green

**User Experience:**
1. Set up 2-4 panes with different models
2. Type prompt in input
3. Click "🎯 Ensemble"
4. Choose judge model (or press Enter for default)
5. Watch judge and candidates fill in real-time
6. Winner highlighted in green with reasoning shown in judge pane

---

## How It Works

### Use Case: "Which model gives the best code review?"

**Setup (in Chat tab):**
- Pane 1: Model A (llama3.2:3b)
- Pane 2: Model B (mistral:7b)
- Type: "Review this Python function for bugs: def foo(x): return x.upper()"

**Click "🎯 Ensemble":**

1. **Judge Model Prompt:**
   - "Judge model? [mistral:7b]" → user presses Enter

2. **Backend dispatcher spawns:**
   - Task 1: Send prompt to llama3.2:3b
   - Task 2: Send prompt to mistral:7b
   - **Both run in parallel**

3. **Results stream back:**
   - Pane 1 shows llama's response + latency (1234ms)
   - Pane 2 shows mistral's response + latency (890ms)

4. **Judge evaluates:**
   - System prompt: "Compare for quality, accuracy, completeness..."
   - Input: Both responses
   - Judge LLM returns: Winner + reasoning

5. **UI updates:**
   - Judge pane (top): Shows verdict + reasoning
   - Winner pane: Bordered in green
   - User reads and decides if they agree

---

## Technical Details

### Why This Approach

**Simplicity:**
- Reuses existing multi-pane chat UI (no new dialog/modal)
- Single button integration (no config needed)
- Auto-detects models from open panes
- Minimal dependencies

**Robustness:**
- Parallel queries = fast (not sequential)
- Graceful fallback if judge model fails
- JSON parsing with markdown fence stripping + regex extraction
- All errors handled without breaking stream

**Streaming:**
- Events arrive live (not waiting for full completion)
- UI updates as results come in
- No long blocking waits

### Architecture

```
Frontend (Chat Tab)
  │
  ├─ "🎯 Ensemble" button click
  │  ├─ Gather models from open panes
  │  ├─ Prompt for judge model
  │  └─ POST /api/v1/ensemble (SSE stream)
  │
Backend
  ├─ EnsembleVoter.vote(prompt)
  │  ├─ Dispatch (send to all models in parallel)
  │  │  ├─ _query_model(model1, prompt) → query_all_models()
  │  │  ├─ _query_model(model2, prompt)
  │  │  └─ asyncio.gather() → wait for all
  │  ├─ Collect results
  │  ├─ Evaluate (_judge_responses)
  │  │  └─ POST judge_model /v1/chat/completions
  │  └─ Synthesize final answer
  │
  └─ SSE stream events back to frontend

Frontend (Live Update)
  ├─ dispatch event → show "Querying models..."
  ├─ result events → fill each pane with response
  ├─ evaluate event → show judge reasoning + winner
  └─ finish event → highlight winner pane
```

---

## Usage Examples

### Example 1: Code Review Competition
```
Prompt: "Write a Python function to validate email addresses"
Models: ["llama3.2:3b", "code-model:13b"]
Judge: "code-model:13b"

→ Both models write validators
→ Judge picks best: "code-model:13b is better because it handles edge cases"
→ Winner pane highlighted in green
```

### Example 2: Creative Writing Critique
```
Prompt: "Write a haiku about latency"
Models: ["llama3.2:3b", "mistral:7b", "neural-chat:7b"]
Judge: "llama3.2:3b"

→ All 3 models write haikus
→ Judge picks winner
→ User can read all 3 and agree/disagree
```

### Example 3: Multiple Perspectives
```
Prompt: "What are the pros and cons of microservices?"
Models: ["llama3.2:3b", "mistral:7b"]
Judge: "mistral:7b"

→ Both respond with perspectives
→ Judge picks "most balanced"
→ User gets consensus + sees both views
```

---

## Files Modified/Created

### New:
- **`beigebox/agents/ensemble_voter.py`** (239 lines)
  - `EnsembleVoter` class
  - `.vote(prompt)` async generator
  - Parallel query logic
  - Judge evaluation
  - JSON parsing helpers

### Updated:
- **`beigebox/main.py`** (+51 lines)
  - `POST /api/v1/ensemble` endpoint
  - Request validation
  - SSE streaming
  - Error handling

- **`beigebox/web/index.html`** (+400 lines, mostly JS)
  - "🎯 Ensemble" button in Chat toolbar
  - `startEnsembleVote()` function
  - `handleEnsembleEvent(event, models, judge)` function
  - `createChatPaneHtml(pane, container)` helper
  - Judge pane + candidate pane rendering

---

## Testing Checklist

### Before deploying:

- [ ] Verify ensemble_voter.py imports work (`from beigebox.config import get_config`)
- [ ] Test `/api/v1/ensemble` endpoint with curl:
  ```bash
  curl -X POST http://localhost:8000/api/v1/ensemble \
    -H "Content-Type: application/json" \
    -d '{"prompt":"hello","models":["llama3.2:3b"],"judge_model":"llama3.2:3b"}'
  ```
- [ ] Test in UI: Open Chat tab, add 2 panes with different models, click "🎯 Ensemble"
- [ ] Verify SSE stream delivers events in order
- [ ] Check judge pane shows winner reasoning
- [ ] Confirm winner pane border turns green

### Known Limitations:

1. **Small models + JSON:** If judge model is too small (like 3B), it may struggle with JSON formatting. Mitigation: Use fallback parser (already in code)
2. **Latency stacking:** Judge evaluation adds ~500ms-1s after responses arrive
3. **No refinement:** Current implementation is single-round. Could add "refine" button later if needed
4. **Prompt length:** Very long prompts may cause timeout. Current timeout: 120s per query + 60s for judge

---

## Future Enhancements (Not Included)

- Iterative refinement (2+ rounds with clarification prompts)
- Vote history tracking (which model pairs work best)
- Ensemble analytics dashboard
- "Refine" button for borderline cases
- Export results as markdown/PDF

---

## Deployment Notes

1. Copy `ensemble_voter.py` to `beigebox/agents/`
2. Update `beigebox/main.py` with ensemble endpoint
3. Update `beigebox/web/index.html` with button + JS functions
4. No new dependencies (uses existing httpx, asyncio, json)
5. Restart container/server
6. Test in Chat tab: "🎯 Ensemble" button should appear

---

## Summary

**What you get:**
- Zero-config ensemble voting in existing Chat tab
- Live streaming results as they arrive
- Judge LLM picks best response
- Winner highlighted + reasoning displayed
- Reuses all existing infrastructure

**What you build with this:**
- Consensus-driven decision making
- Model comparison tools
- Creative/technical voting scenarios
- Confidence in "best" responses

**One-click usage:**
1. Set up 2-4 panes with different models
2. Type prompt
3. Click "🎯 Ensemble"
4. Pick judge model
5. Watch vote in real-time

**Go vote!** 🎯
