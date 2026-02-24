# Session Notes — v1.0 Harness Sprint

## What was built this session

### 1. Multi-pane Chat Tab

The Chat tab (tab 2) now supports n+1 output windows. Architecture:

- **Pane state**: `chatPanes[]` array, each entry: `{id, target, history[]}`
- **Pagination**: max 4 panes visible per page (`PANES_PER_PAGE = 4`). Navigate with `[` / `]` keys or ◀ ▶ buttons in the toolbar
- **Adding panes**: `＋` button in toolbar. Soft cap at 20 panes total. New pane auto-navigates to its page
- **Removing panes**: `－` removes the last pane (not below 1)
- **Per-pane target selector**: each pane has a dropdown — pick a model or `@operator`. Populated from `/v1/models`
- **Fan-out**: when user hits Send, the message goes to ALL visible panes simultaneously (`Promise.all`)
- **@op prefix**: typing `@op <message>` routes only to operator-targeted panes (or all if none set to @op)
- **Streaming per pane**: each pane streams independently — model panes use SSE streaming, operator panes use REST
- **Clear**: per-pane ✕ button, or "Clear all" in toolbar

### 2. Operator Tab — Agent/Backend Selector

The Operator tab now has a dropdown (`#op-target`) to pick:
- `operator (default)` — routes to `/api/v1/operator` (LangChain ReAct agent)
- `model: <id>` — routes directly to `/v1/chat/completions` for that model (non-streaming, single turn)

Populated dynamically from `/v1/models` on tab switch. The label in the op-log shows which target was used per query.

### 3. Harness Tab (tab 7, Config moved to tab 8)

New `#panel-harness` tab for parallel agent launching. Features:

- **Target chips**: add models/operator via dropdown + ＋ Add button. Chips shown with ✕ to remove
- **Prompt textarea**: `Ctrl+Enter` to run (or use ▶ Run All button)
- **Parallel execution**: all targets fire simultaneously with `Promise.all(harnessTargets.map(...))`
- **2×2 grid**: panes display in a CSS flex grid, 2 per row, max 4 per view page
- **Live streaming**: model panes stream token-by-token, updating in realtime. Operator panes show result on completion
- **Status indicators**: `pending` (yellow, italic), `done` (green), `error` (red) per pane
- **Pagination**: `{` / `}` keys or ◀ ▶ buttons. Page indicator shows `p1/N`
- **Clear**: resets all results but keeps targets in place

### 4. Tab renumbering

| Key | Tab |
|-----|-----|
| 1 | Dashboard |
| 2 | Chat |
| 3 | Conversations |
| 4 | Flight Recorder |
| 5 | Tap |
| 6 | Operator |
| 7 | Harness (new) |
| 8 | Config |

### 5. Shared model loading

`loadModelsForPanes()` now populates:
- Per-pane target selectors in Chat tab
- `#op-target` in Operator tab
- `#harness-add-select` in Harness tab

All from a single `/v1/models` call.

## Files changed this session

| File | Change |
|------|--------|
| `beigebox/web/index.html` | Multi-pane chat, operator selector, Harness tab, tab renumber |

## What's not done yet (needs a backend endpoint)

The `/api/v1/harness` backend endpoint was NOT added — the frontend routes directly to `/v1/chat/completions` and `/api/v1/operator` per target. This is intentional: it keeps each pane's streaming independent and simpler. A future `/api/v1/harness` could be added if we want server-side orchestration (e.g. for logging all harness runs together in the wiretap, or for non-browser callers).

## Notes for next session

- Conversation forking is still on the roadmap — the Harness tab is the precursor UI for ensemble/fork views
- The harness currently uses the single operator instance. When multi-operator-instance support lands, the harness add-select should enumerate named operator configs
- Consider adding a "Save harness run" button that writes all pane outputs to a conversation record
- Voice (tab 8 → push-to-talk) was planned for v1.0 — now Config shifted to 8; voice could go to 9 or fold into the operator/harness workflow

*Line's quiet. Talk soon.*
