# Ensemble Voting Implementation — Difficulty & Approach

**Date**: Feb 21, 2026  
**Status**: Analysis & Recommendation

---

## Overview

Ensemble voting on responses is **surprisingly feasible** given BeigeBox's current architecture. Difficulty: **Medium (4-6 hours)**.

The HarnessOrchestrator already handles the core logic (dispatch → evaluate → synthesize). The multi-pane chat UI is already designed for parallel model responses. We just need to bridge them and add a "Judge" UI control.

---

## Current State (Already in Place)

### 1. **Multi-Pane Chat UI** ✅
- `Chat` tab supports 1-4 panes per page, unlimited pages
- Each pane has independent model selector (`pane-model-<id>`)
- `sendChat()` fans out message to all visible panes in parallel
- `sendToPane()` routes to either operator or backend model
- History is tracked per pane: `pane.history = [{role, content, model}]`

### 2. **Harness Orchestrator** ✅
```python
HarnessOrchestrator:
  - .run(goal) → async generator yielding events
  - _dispatch() → runs tasks in parallel (asyncio.gather)
  - _evaluate() → LLM judges results against goal
  - _synthesize() → produces final answer from all results
```

This is **exactly** the pattern ensemble voting needs.

### 3. **Backend Infrastructure** ✅
- Parallel routing already works (multi-backend failover)
- Cost tracking per model
- Conversation storage with model tracking
- All models accessible via same `/v1/chat/completions` endpoint

---

## What's Missing

### UI Layer (30% effort)
1. **"Run Ensemble" button** in Chat tab
   - Opens a dialog or panel
   - Select N models from available list
   - Input max rounds for refinement (1-3)
   - Pick judge model (what evaluates all responses)
   
2. **Judge pane** (top, highlighted)
   - Shows judge reasoning: "Comparing responses for quality, accuracy, completeness..."
   - Winner verdict: "Model X provides the best response because..."
   - Synthesized best answer

3. **Candidate panes** (bottom grid)
   - Each shows responses from candidate models
   - Color code: green (judge picked this), gray (runner-up)
   - Show judge's critique for each

### Backend Endpoint (50% effort)
- **`POST /api/v1/ensemble`** — new endpoint
  - Input: `{prompt, models: [list], judge_model, max_rounds: 1-3}`
  - Returns: SSE stream of events (like harness)
  - Events: `{type: "dispatch"|"evaluate"|"finish", ...}`
  
- **Reuse HarnessOrchestrator logic but specialize it:**
  - Plan phase: "Send prompt to all N models in parallel"
  - Dispatch phase: Fan to all models
  - Evaluate phase: Judge LLM compares responses
  - Finish: Return best + synthesis

### Optional: Iterative Refinement (20% effort)
- Judge says "These are borderline. Ask a clarification and retry."
- Second round: send refined prompt to top 2 candidates
- Judge picks winner after round 2

---

## Implementation Plan

### Step 1: Add Ensemble Endpoint (2-3 hours)

**New file or method in `main.py`:**

```python
@app.post("/api/v1/ensemble")
async def ensemble_vote(request: Request):
    """
    Multi-model ensemble with LLM judge.
    
    Request body:
    {
      "prompt": "What is X?",
      "models": ["llama3.2:3b", "mistral:7b"],
      "judge_model": "llama3.2:3b",
      "max_rounds": 1
    }
    
    Returns: SSE stream of JSON events
    """
    # Validate models exist
    # Create HarnessOrchestrator with goal = prompt
    # Set available_targets = models list
    # Customize system prompt for ensemble pattern
    # Stream events back to client
```

**Key: Adapt HarnessOrchestrator for this use case:**
- Round 1: Orchestrator tells all models to respond to prompt (single task per model)
- Evaluate: Judge LLM compares all responses
- Finish: Judge picks best + synthesizes

### Step 2: Wire UI (2-3 hours)

In `index.html` Chat tab:

```html
<!-- Add button near toolbar -->
<button onclick="openEnsembleDialog()">🎯 Ensemble Vote</button>

<!-- Dialog/modal with: -->
<!-- - Multi-select for models -->
<!-- - Judge model picker -->
<!-- - Max rounds slider (1-3) -->
<!-- - "Run Ensemble" button -->
```

When user clicks "Run Ensemble":
1. Fetch `/api/v1/ensemble` with SSE
2. Create judge pane (cyan border, top center)
3. Create candidate panes below for each model
4. Stream events → update panes live:
   - `dispatch` event: show "Querying N models..."
   - `result` events: populate pane with each response
   - `evaluate` event: judge reasoning + verdict
   - `finish` event: show final answer + winner

### Step 3: Test & Polish (1-2 hours)
- Test with 2-3 models
- Error handling (model timeouts, judge failures)
- Mobile responsiveness (ensemble dialog needs to work on small screens)

---

## Why This Is Feasible

1. **HarnessOrchestrator is 90% of the work** — it already does dispatch/evaluate/synthesize
2. **Backend parallelization is proven** — multi-pane chat fans out without issues
3. **SSE streaming is already in place** — operator tab uses it, orchestrator uses it
4. **UI layout is ready** — multi-pane grid can accommodate judge + candidates
5. **Judge LLM is just another `/v1/chat/completions` call** — no new infrastructure

---

## Difficulty Breakdown

```
Backend (HarnessOrchestrator + new endpoint):  2-3 hours (Medium)
  - Specialize orchestrator for ensemble pattern
  - Add /api/v1/ensemble endpoint
  - Wire to cost tracking, conversation storage

Frontend (UI + dialog + SSE integration):      2-3 hours (Medium)
  - Ensemble dialog (modal/panel)
  - Model selector (multi-select)
  - Judge + candidate panes
  - Event stream handling

Testing & edge cases:                          1-2 hours (Easy-Medium)
  - Timeout handling
  - Bad judge responses
  - Model failures mid-vote
  - Mobile layout

Total: 5-8 hours, realistic delivery in one focused session.
```

---

## Alternatives & Trade-offs

### Option A: **Lightweight** (2-3 hours)
- No new endpoint, reuse Harness manually
- User clicks "Harness" → orchestrated mode
- Set prompt to: "Send this exact prompt to [model1], [model2], [model3]. Then judge which is best."
- **Pro**: Zero backend work  
- **Con**: Clunky UX, requires manual prompt engineering

### Option B: **Medium** (5-8 hours) ← **Recommended**
- New `/api/v1/ensemble` endpoint
- Dedicated UI in Chat tab
- Automatic prompt routing + judge call
- **Pro**: Seamless UX, one-click voting  
- **Con**: Requires backend specialization

### Option C: **Full-Featured** (10+ hours)
- Iterative refinement (2+ rounds)
- Historical vote tracking (best judge verdicts per query)
- Ensemble analytics (which model pairs work best together)
- Voting on tool choices (not just responses)
- **Pro**: Production-grade  
- **Con**: Overkill for MVP

---

## Recommendation

**Go with Option B (Medium).** 

Hardest part is prompt engineering the judge LLM to output parseable JSON. Easiest mitigation: copy the `_parse_json` fallback logic from HarnessOrchestrator (already handles markdown fences, regex extraction).

**Next Steps if you want to build this:**
1. Copy the HarnessOrchestrator pattern
2. Create a specialized `EnsembleVoter` class (or method in existing orchestrator)
3. Add `/api/v1/ensemble` endpoint (inherit SSE streaming from harness endpoint)
4. Wire UI dialog + panes + judge display
5. Test with 2 small models (fast iteration)

---

## Files Affected (if we build it)

```
beigebox/agents/ensemble_voter.py    NEW (or extend harness_orchestrator.py)
beigebox/main.py                     +ensemble endpoint
beigebox/web/index.html              +ensemble button, dialog, judge pane UI
tests/test_ensemble.py               NEW (basic tests)
```

**Ship without:** tests initially, full refinement logic, vote analytics.

---

## Quick Win: Start with Harness Manual Ensemble

If you want to test the concept **today** without code changes:

1. Open Chat tab
2. Add 2 panes (one for each model)
3. Type in message
4. Both panes show responses side-by-side
5. Copy both responses to Harness tab
6. Write prompt: "Compare these two responses on quality/accuracy/completeness: [response1] vs [response2]. Which is better?"
7. Run Harness → see judge reasoning

This proves the concept works with zero code. Then if you like it, build the automated `ensemble` endpoint.

---

## Bottom Line

**Difficulty: Medium. Effort: 5-8 hours. Viability: Very High.**

You already have all the pieces. Just need to glue them together and add a nice UI wrapper.
