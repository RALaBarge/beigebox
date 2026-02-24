# Session Notes — Final Feature Sweep
**Date:** 2026-02-24
**Scope:** Conversation export, harness fixes, TTS operator wiring. Clears the roadmap.

---

## 1. Conversation Export

**New methods in `beigebox/storage/sqlite_store.py`:**
- `export_jsonl(model_filter)` — `{"messages": [{role, content}]}` per conversation, true JSONL (newline-delimited)
- `export_alpaca(model_filter)` — `{"instruction", "input", "output"}` per turn pair
- `export_sharegpt(model_filter)` — `{"id", "conversations": [{from: "human"|"gpt", value}]}` per conversation

All three filter out system messages and require at least one user+assistant pair to include a conversation.

**New endpoint in `beigebox/main.py`:**
```
GET /api/v1/export?format=jsonl|alpaca|sharegpt&model=<optional>
```
Returns a file download with correct Content-Disposition header. JSONL returned as `application/x-ndjson`, others as `application/json`.

**Web UI (`beigebox/web/index.html`):**
- Format selector (JSONL / Alpaca / ShareGPT) added to Conversations tab toolbar
- `↓ Export` button triggers download via fetch → Blob → `<a>` click pattern
- Status shown inline in toolbar label

---

## 2. Harness Orchestrator — `localhost` → `127.0.0.1`

**File:** `beigebox/agents/harness_orchestrator.py`, `_run_operator()`

Changed self-call URL from `http://localhost:{port}/api/v1/operator` to `http://127.0.0.1:{port}/api/v1/operator`.

Inside Docker containers, `localhost` can resolve differently depending on `/etc/hosts` and networking mode. `127.0.0.1` always means the container's own loopback and is unambiguous. Port is still read from `cfg["server"]["port"]` (default 8000).

---

## 3. Harness `_parse_json` — Small Model JSON Reliability

**File:** `beigebox/agents/harness_orchestrator.py`, `_parse_json()`

Previous implementation handled markdown fences and embedded object extraction. Small models (3b range) also produce:
- Trailing commas before `}` or `]`
- Truncated output (hit token limit mid-JSON)

New implementation adds:
1. Trailing comma cleanup via regex (`re.sub(r',\s*([}\]])', r'\1', s)`) applied as fallback before giving up
2. Truncation recovery — walks the string counting brace depth, appends missing `}` characters, re-attempts parse
3. Applied consistently: direct parse → cleaned parse → extracted object → cleaned extracted → truncation recovery → fallback

Added `@staticmethod` decorator (was missing — functionally fine since it never used `self` but cleaner).

---

## 4. TTS Auto-play in Operator Tab

**File:** `beigebox/web/index.html`, `runOp()` function

`speakText()` is now called after both operator response paths:
- Model path (routed to `/v1/chat/completions`): `speakText(answer)` after response renders
- Operator path (`/api/v1/operator`): `speakText(d.answer)` on success

Error responses do not trigger TTS (intentional — you don't want the box speaking error messages).

---

## Roadmap Status

Everything is done. Roadmap is fully cleared except the full test run.

Remaining known edge cases (low priority, no action taken):
- Session cache eviction is correct and already in place — the review doc concern was pre-existing and already resolved
- Harness small model reliability improved by this session's `_parse_json` hardening

---

*Tap the line. — Claude, Feb 24 2026*
