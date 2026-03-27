# ⚠️ PARTIAL — Duplicate of CODEBASE-AUDIT.md findings. Same status: quick wins applied, strategic refactors deferred.

# Code Quality Audit — 2026-03-16

Audit of three files ordered by complexity (harness → proxy → index.html).
No changes made. This document is the action list.

---

## 3 — `beigebox/agents/harness_orchestrator.py`

**Overall verdict:** Clean. Well-structured, `_wire()` helper solid. Minor issues only.

| # | Line | Severity | Issue |
|---|------|----------|-------|
| 1 | 178 | Low | `while` loop body indented 2 spaces instead of 4 — inconsistent with rest of file |
| 2 | 31 | Low | `uuid` imported twice (`import uuid` and `from uuid import uuid4`) — only `uuid4` is used, remove the first |
| 3 | 383 | Low | `resp.ok` / `resp.content` / `resp.status_code` used on `backend_router.forward()` return — custom response object, attribute contract is implicit and undocumented |
| 4 | 504–515 | Low | Unreachable safety-net return after `for attempt in range(...)` — loop always returns inside; dead code |
| 5 | 45 vs 563 | Medium | `"rate_limit"` is in `NON_RETRYABLE_ERRORS` but the docstring at line 563 says `"retryable but with longer backoff"` — contradiction |
| 6 | 74–75 | Low | `get_config()` / `get_runtime_config()` called in `__init__` and stored — if runtime config changes, `self.model` won't update for the lifetime of the object |

---

## 2 — `beigebox/proxy.py`

**Overall verdict:** Most critical file in the repo. Well-commented but ~1600 lines. Several real bugs.

### High Severity

| # | Lines | Issue |
|---|-------|-------|
| H1 | 108–112 | **Race condition** in `_get_session_model()` — check-then-delete without lock; concurrent async tasks can raise `KeyError`. Fix: `asyncio.Lock` around cache reads/writes. |
| H2 | 129–132 | **Race condition** in `_evict_session_cache()` — `sorted()` then delete loop; dict can be mutated between the two by another coroutine. Same fix. |
| H3 | 1118 | `response` variable undefined in the non-router path but referenced conditionally — safe by accident. Any future refactor of that condition will crash. |

### Medium Severity

| # | Lines | Issue |
|---|-------|-------|
| M1 | 486, 511 | `body.pop(BB_RULE_TAG)` then conditionally restores it — pop-then-restore is fragile; key is absent during any intervening code that reads `body` |
| M2 | 1245–1247 | Semantic cache hits bypass conversation logging, cost tracking, and post-hooks entirely — likely intentional but invisible and undocumented |
| M3 | 1287, 1310, 1358 | Silent `except` blocks swallow cost/JSON parse failures with no log at any level |
| M4 | 1471–1472 | Bare `except: pass` in `list_models()` — hides all Ollama backend failures silently |
| M5 | 1277–1311 vs 1315–1359 | ~20 lines of SSE chunk parsing duplicated verbatim between router path and direct path — extract `_parse_sse_chunk()` |

### Low Severity

| # | Lines | Issue |
|---|-------|-------|
| L1 | 1203 | `import json as _json` locally while `json` already imported at module level (line 20); both `json.dumps()` and `_json.dumps()` used in the same function |
| L2 | 230, 279 | Fire-and-forget embed tasks use `lambda t: t.exception() and logger.warning(...)` — relies on exception object truthiness, non-obvious pattern |
| L3 | 125–127 | Session cache eviction triggers on every 100th write — can cause latency spike under burst load |

---

## 1 — `beigebox/web/index.html` (JavaScript)

**Overall verdict:** Functional but has accumulated real bugs. Single-file constraint makes refactoring painful.

### Critical

| # | Location | Issue |
|---|----------|-------|
| C1 | `enableVoiceUI()` | Adds `keydown` listener to `document` on every call, never removes it — hotkey fires N times after N calls. Fix: store handler ref, call `removeEventListener` before re-adding. |
| C2 | `sendChat()` | `sendBtn.disabled = true` with no null check — crashes if element is missing |
| C3 | `runOp()` | Same pattern — 3 element accesses with no null guards |

### High

| # | Location | Issue |
|---|----------|-------|
| H1 | `initCfgTooltips()` | Adds `mouseover`/`mousemove`/`mouseout` to `document` at init, never removed — fires DOM traversal on every mouse move for lifetime of page |
| H2 | `pollHealth()` | Accesses `proxy-dot` and `proxy-status` with no null checks in both `try` and `catch` branches |
| H3 | Multiple | `populateModelSelect` pattern copy-pasted 6+ times: `councilPopulateModels`, `loadModelsForPanes`, `harnessEnsemblePopulateModels`, etc. Extract one `populateModelSelect(el, models)` helper. |
| H4 | Streaming loop | Bare `catch {}` around SSE chunk JSON parse — silently drops malformed chunks, makes debugging stream issues impossible |

### Medium

| # | Location | Issue |
|---|----------|-------|
| M1 | `stopRecording()` | If `_mediaRecorder.stop()` throws, `_isRecording` resets but media stream is not released — needs try-finally |
| M2 | `_loadHintId` | Module-level var assumes one active hint at a time — rapid successive messages can cause second hint removal to target wrong element |
| M3 | `chatPanes[start] \|\| chatPanes[0]` | Returns `undefined` if `chatPanes` is empty — no length check |
| M4 | `_ttsCurrentSource.onended` | Fires after component may be removed from DOM — queries possibly-deleted elements |

---

## Prioritized Fix Order

1. **proxy.py H1/H2** — `asyncio.Lock` on `_session_cache` reads/writes (race condition, can corrupt state under load)
2. **index.html C1** — Voice UI listener leak (accumulates handlers, hotkey misfires)
3. **index.html C2/C3** — Null guards in `sendChat` / `runOp` (hard crash)
4. **proxy.py M5** — Extract `_parse_sse_chunk()` (pure duplication, maintenance risk)
5. **index.html H3** — Extract `populateModelSelect()` helper (6 copy-paste sites)
6. **harness_orchestrator.py #5** — Fix `rate_limit` retryability contradiction (docs vs code disagree)
7. **proxy.py M2** — Document semantic cache bypass behavior (invisible to operators)
8. **proxy.py M3/M4** — Add logging to silent `except` blocks
