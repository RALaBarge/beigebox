# Tap Redesign — Interactive Trace Surface

Design doc. Figure out where everything lives before building the pipeline.

---

## Current state

- `WireLog` writes JSONL to `data/wire.jsonl`, line-buffered (real-time)
- Fields: `ts, dir, role, model, conv, len, tokens, content, tool, latency_ms, timing`
- Web UI: `/api/v1/tap?n=N`, filters by role/dir, live poll, conv ID highlight already works
- Only covers proxy `/v1/chat/completions` traffic — operator/harness/routing invisible

---

## Where data lives

**Problem with pure JSONL:** append-only, no cross-reference. To highlight everything
related to a `run_id` you'd have to scan the whole file.

**Decision: SQLite + JSONL dual write**

| Store | Purpose |
|---|---|
| `wire.jsonl` | Raw append stream — stays as-is, `beigebox tap` CLI still works |
| `wire_events` SQLite table | Indexed, queryable — powers the web UI cross-linking |

SQLite schema:
```sql
CREATE TABLE wire_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    event_type  TEXT NOT NULL,   -- see event types below
    source      TEXT NOT NULL,   -- 'proxy', 'operator', 'harness', 'router', 'cache', 'classifier'
    conv_id     TEXT,            -- conversation ID (nullable — not all events have one)
    run_id      TEXT,            -- harness/operator run ID
    turn_id     TEXT,            -- single turn within a run
    tool_id     TEXT,            -- individual tool call
    model       TEXT,
    role        TEXT,
    content     TEXT,            -- truncated to 2000 chars
    meta        TEXT,            -- JSON blob for event-specific fields (score, elapsed_ms, etc.)
    misc1       TEXT,            -- spare field — use for whatever
    misc2       TEXT             -- spare field — use for whatever
);
CREATE INDEX ix_wire_conv   ON wire_events(conv_id);
CREATE INDEX ix_wire_run    ON wire_events(run_id);
CREATE INDEX ix_wire_type   ON wire_events(event_type);
CREATE INDEX ix_wire_ts     ON wire_events(ts);
```

`meta` JSON fields vary by event type — keeps the schema stable as we add events.

---

## Event types

### Already exists (proxy)
| event_type | source | Key meta fields |
|---|---|---|
| `message` | proxy | dir, tokens |
| `tool_call` | proxy | tool_name |

### New — Routing
| event_type | source | Key meta fields |
|---|---|---|
| `routing_decision` | router | tier (zcommand/session/cache/classifier/decision/default), model_chosen, backends_tried, elapsed_ms |
| `cache_hit` | cache | similarity_score, cache_key |
| `cache_miss` | cache | similarity_score, query_preview |
| `classify_result` | classifier | model_chosen, similarity_score, category |
| `decision_result` | decision_llm | model_chosen, reasoning_preview, elapsed_ms |
| `session_hit` | proxy | model, ttl_remaining_s |

### New — Operator
| event_type | source | Key meta fields |
|---|---|---|
| `op_start` | operator | run_id, model, question_preview |
| `op_thought` | operator | run_id, turn_id, thought_text, iteration |
| `op_tool_call` | operator | run_id, turn_id, tool_id, tool_name, input_preview |
| `op_tool_result` | operator | run_id, turn_id, tool_id, tool_name, result_preview, elapsed_ms |
| `op_loop_nudge` | operator | run_id, turn_id, nudge_reason |
| `op_answer` | operator | run_id, total_iterations, total_elapsed_ms, answer_preview |
| `op_error` | operator | run_id, error |

### New — Harness
| event_type | source | Key meta fields |
|---|---|---|
| `harness_start` | harness | run_id, model, task_preview |
| `harness_turn` | harness | run_id, turn_id, iteration, tokens, elapsed_ms |
| `harness_inject` | harness | run_id, injected_content_preview |
| `harness_end` | harness | run_id, total_turns, total_elapsed_ms |

### New — System
| event_type | source | Key meta fields |
|---|---|---|
| `model_load` | proxy | model, backend, load_time_ms |
| `backend_fail` | router | backend_name, error, model |
| `backend_recover` | router | backend_name |

---

## Clickable IDs — what highlights what

| Click this | Highlights |
|---|---|
| `conv_id` | All messages in that conversation |
| `run_id` | All events for that operator/harness run (thoughts, tool calls, answer) |
| `tool_id` | The tool_call and its matching tool_result |
| `model` badge | All events using that model |
| `source` badge | All events from that subsystem |

Click again to deselect (toggle, already how conv works).

---

## Interactive overrides (phase 2 — after logging is solid)

Things you should be able to click and change:

| Element | Override action |
|---|---|
| `routing_decision` model | Override model for next request |
| `classify_result` | Adjust classifier threshold |
| `op_tool_call` | Re-run tool with edited input |
| `cache_hit` | Bypass cache for this query |
| `op_thought` | Inject a redirect into the operator loop |

These write back via existing API endpoints or new ones. Don't build until
the events are flowing and UI is showing them — you need to see the data first.

---

## UI changes needed

### Tap panel additions
- Filter by `source` (proxy / operator / harness / router / cache)
- Filter by `run_id` — type/paste or click from a run list
- Group mode: cluster events by `run_id` with collapsible sections
- Per-event-type color coding (routing = purple, operator = yellow, harness = teal, errors = red)
- `meta` fields shown inline on expand (score, elapsed, iteration, etc.)
- Clickable `run_id`, `conv_id`, `tool_id`, `model` chips — highlight matching rows

### Entry card anatomy
```
[HH:MM:SS] [source] [event_type] [model?] [run_id chip?] [conv_id chip?]
  ↳ content preview
  ↳ [expand] score: 0.87 · elapsed: 234ms · iteration: 3
```

---

## Build order

1. **Schema** — add `wire_events` table to SQLiteStore migrations
2. **WireLog.log_event()** — new method that writes to both JSONL and SQLite
3. **Proxy events** — wire existing `wire.log()` calls through new method, add `routing_decision`
4. **Operator events** — `op_start`, `op_thought`, `op_tool_call`, `op_tool_result`, `op_answer`
5. **Router events** — `cache_hit/miss`, `classify_result`, `decision_result`
6. **Harness events** — `harness_start`, `harness_turn`, `harness_end`
7. **API** — update `/api/v1/tap` to query SQLite, add `run_id` / `source` filter params
8. **UI** — new chips, filters, group mode, expand for meta fields
9. **Interactive overrides** — phase 2, after you've seen the data flowing
