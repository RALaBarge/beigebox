# Step 4: Ensemble Voting UI Integration

**Problem**: Backend supports ensemble voting (`ensemble_voter.py` exists) but web UI has no way to trigger it.

**Solution**: Add "🎯 Ensemble" button to Chat tab that:
1. Opens modal to select models (min 2)
2. Lets user pick a judge model
3. Sends prompt to all models in parallel
4. Judge LLM evaluates and ranks responses
5. Display winning response + all others side-by-side

---

## Files Changed

### MODIFIED FILE: `beigebox/web/index.html`

**Changes**:

1. **Added Ensemble button to Chat toolbar** (line ~1200)
   ```html
   <button onclick="showEnsembleDialog()" title="Ensemble voting: send to multiple models and judge">🎯 Ensemble</button>
   ```

2. **Added Ensemble Modal Dialog** (line ~1420)
   - Model selection checkboxes (min 2)
   - Judge model dropdown
   - "Run Ensemble" button
   - Clean, responsive design

3. **Added JavaScript Functions** (line ~3215)
   - `showEnsembleDialog()` — shows modal, populates model list
   - `closeEnsembleDialog()` — closes modal, resets state
   - `runEnsemble()` — sends request to `/api/v1/ensemble` endpoint, streams results back
   - Results rendering with green highlight on winner

**Lines added**: ~230 (HTML modal + JS functions)

---

## How It Works

### Flow:

1. **User clicks "🎯 Ensemble"** button in Chat tab
2. **Modal pops up** showing:
   - List of available models with checkboxes
   - Judge model dropdown
   - Cancel / Run Ensemble buttons

3. **User selects** at least 2 models + judge model
4. **Clicks "Run Ensemble"**
5. **Request sent** to `POST /api/v1/ensemble`:
   ```json
   {
     "models": ["llama3.2:3b", "mistral:7b"],
     "judge_model": "llama3.2:3b",
     "prompt": "What is machine learning?"
   }
   ```

6. **Backend** (existing `EnsembleVoter`):
   - Sends prompt to all models in parallel
   - Collects all responses
   - Asks judge to evaluate & pick best
   - Streams events back via SSE

7. **Frontend** displays:
   - Winner in green with verdict
   - All responses below, winner highlighted with green border
   - Clean card layout

---

## Example Use Cases

### 1. Code Review
**Models**: llama3.2:3b, deepseek-coder:7b  
**Judge**: llama3.2:3b  
**Prompt**: "Review this function for bugs"  
→ Judge picks best code analysis

### 2. Factual Question
**Models**: mistral:7b, neural-chat:7b  
**Judge**: llama3.2:3b  
**Prompt**: "What was the first programming language?"  
→ Judge picks most accurate answer

### 3. Creative Writing
**Models**: openchat:3.5, llama-zephyr:7b  
**Judge**: openchat:3.5  
**Prompt**: "Write a haiku about AI"  
→ Judge picks best poem (usually both are good, but one may be more poetic)

---

## UI Appearance

### Modal:
```
┌─────────────────────────────┐
│ 🎯 Ensemble Voting        ✕ │
│                             │
│ Send the same prompt to... │
│                             │
│ Select Models (check ≥2):  │
│ ☑ llama3.2:3b              │
│ ☐ mistral:7b               │
│ ☐ neural-chat:7b           │
│                             │
│ Judge Model:                │
│ [llama3.2:3b ▼]            │
│                             │
│       [Cancel]  [Run]      │
└─────────────────────────────┘
```

### Results:
```
┌──────────────────────────────┐
│ 🏆 Winner: mistral:7b        │
│                              │
│ Judge Verdict:               │
│ "This response is most      │
│  accurate and well-explained│
│  compared to others..."      │
│                              │
│ Responses:                   │
│ ┌──── mistral:7b ────────┐  │
│ │ [Winner] Complete...   │  │ ← Green border
│ │ explanation of ML...   │  │
│ └────────────────────────┘  │
│                              │
│ ┌──── llama3.2:3b ───────┐  │
│ │ Machine learning is... │  │
│ └────────────────────────┘  │
└──────────────────────────────┘
```

---

## Responsive Design

- **Desktop**: Modal centered, full-size result cards
- **Tablet**: Modal 90% width, single-column results
- **Mobile**: Modal full-screen, scrollable, touch-friendly

---

## Performance

- **Parallel dispatch**: All models queried simultaneously (no sequential waits)
- **Streaming**: Results stream back as they arrive (don't wait for all)
- **Judge latency**: Typically 1-5 seconds for evaluation
- **Total time**: ~3-10 seconds depending on model sizes

Example timeline:
```
t=0s:    Send to all 3 models in parallel
t=2s:    Model 1 responds
t=3s:    Model 2 responds
t=4s:    Model 3 responds, start judge
t=6s:    Judge finishes, display results
```

---

## Configuration

No configuration needed. Ensemble uses:
- Models from `/v1/models` endpoint (auto-populated)
- Default backend URL from config
- Existing `EnsembleVoter` class (no changes)

---

## Error Handling

- **<2 models selected**: Alert "Please select at least 2 models"
- **No prompt**: Alert "Please enter a prompt"
- **Network error**: Error box in pane: "Error: Network failure"
- **Empty response**: Graceful display of empty responses

---

## Testing

### Test 1: Basic Ensemble
1. Click "🎯 Ensemble" button
2. Select 2+ models
3. Enter prompt: "What is Python?"
4. Click "Run Ensemble"
5. Should see: Winner highlighted, all responses displayed

### Test 2: Mobile
1. Open on iPhone/Android
2. Click "🎯 Ensemble"
3. Modal should be full-screen, scrollable
4. Results should be single-column, readable

### Test 3: Cancel
1. Click "🎯 Ensemble"
2. Click "Cancel" (or ✕ button)
3. Modal should close, models unselected

### Test 4: Multiple Ensembles
1. Run one ensemble (pick models A, B)
2. Run another (pick models B, C)
3. Should work fine, separate panes created

---

## Deployment

1. **Replace `beigebox/web/index.html`** with updated version
2. **No backend changes** — endpoint already exists
3. **No config changes** — uses existing models
4. **Restart BeigeBox**

That's it.

---

## Future Enhancements

If you want to extend this later:
- Add confidence score from judge
- Add timing breakdown per model
- Add "re-run ensemble" button on existing result
- Add ensemble history/replay
- Add weighting (some models count more)
- Add custom judge prompt template

---

This completes Step 4. **File ready**:
- `index.html` → `beigebox/web/`

All backend logic already exists and works. This just surfaces it in the UI.
