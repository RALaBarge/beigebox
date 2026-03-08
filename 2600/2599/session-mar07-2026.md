# Session — March 7, 2026 (v1.2.3)

## Topics covered

1. Operator — Qwen agent patterns (TIR, ReAct fallback, inject-then-acknowledge memory)
2. Council UX — close cards, kill button, model affinity submission order
3. BrowserBox — root-cause diagnosis and fix for calls never reaching the extension

---

## 1. Operator Qwen Patterns

### TIR — Python Code Interpreter (`beigebox/tools/python_interpreter.py`)

New tool registered as `python`. Implements the Tool-Integrated Reasoning pattern:
the operator model can emit Python code, it runs in a bwrap sandbox, stdout/stderr
come back as the next observation.

Key decisions:
- **stdin-pipe** approach: code injected via `stdin` to `python3 -`. No tempfile binding
  needed — sidesteps the bwrap tmpfs problem (host /tmp and sandbox /tmp are different).
- **bwrap profile**: same as system_info — no network, no /app or /home, ro /workspace/in,
  rw /workspace/out. Falls back to unsandboxed only if `allow_unsandboxed: true` in config.
- **Config-gated**: `tools.python_interpreter.enabled: false` by default (requires bwrap).
- **8KB output cap** + 10s timeout; both configurable via `tools.python_interpreter.*`.

Wired into `registry.py` as `tools["python"]`.

### ReAct Fallback (`_extract_react()` in operator.py)

When `_extract_json` fails, `_extract_react` is tried before nudging or giving up.
Parses `Thought: / Action: / Action Input: / Final Answer:` patterns.
Returns the same `{tool, input, thought}` / `{answer}` dict shape as the JSON path,
so the rest of the loop is unchanged. Fires in both `run()` and `run_stream()`.

Why: some models (llama3.x, smaller Qwens) reliably emit ReAct text but not JSON.
The fallback recovers these without extra LLM calls.

### Inject-then-Acknowledge Memory (`_load_notes()` in operator.py)

The operator can write `workspace/out/operator_notes.md` during a session.
On the next session start:
1. Notes are injected as a `user` message: `[NOTES FROM PREVIOUS SESSIONS]\n...\n[END NOTES]`
2. A **synthetic** assistant acknowledgment is appended: `{"thought": "I have reviewed...", "status": "ready"}`

No extra LLM call — this is just conversation priming. The model sees the notes as
already-acknowledged context before the first user message arrives. Particularly
effective with Qwen3 which tends to ignore system prompts but honors prior conversation.

System prompt updated to tell the operator it can write notes for cross-session persistence.

---

## 2. Council UX

### Close individual cards

Added `✕` button to each card header. Calls `this.closest('.council-card').remove()`.

**Critical fix**: `_councilGetCards()` was previously using `document.getElementById('council-model-${i}')` where `i` is the forEach index. After card removal, the remaining cards have non-contiguous IDs, so the wrong models/tasks were being read.

Fixed: query `.council-card-model` and `.council-card-task` directly within each `.council-card` element. Index is irrelevant — works correctly after any removal pattern.

### Kill button

`councilEngage()` now creates an `AbortController` before the fetch. The signal is
passed to `fetch(..., { signal })`. A `■ Stop` button is shown in the engage row
only while running, hidden on completion or abort.

`councilKill()` calls `_councilAbort.abort()`. The `AbortError` is caught silently
(no error message shown to user). UI resets — engage button re-enabled, kill button hidden.

### Model affinity batching (`council.py execute()`)

Council members are sorted by model and grouped with `itertools.groupby`. Members
sharing a model run in parallel (asyncio tasks); groups are dispatched sequentially.

Why: Ollama has only one model resident in VRAM at a time. If council has
[qwen, llama, qwen, llama], Ollama does 4 model swaps. Sorted to [qwen, qwen, llama, llama]
reduces it to 1 swap. For 4-member councils with 2 models this halves latency.

Refactored: `run_member` is now a top-level `_run_member(member, backend_url, query, queue)`
coroutine. Avoids Python closure capture bugs when defining coroutines inside a for loop
(the `q=queue` default-arg trick was replaced with explicit argument passing).

---

## 3. BrowserBox root-cause diagnosis and fix

### Root cause

Chrome MV3 service workers can be killed by the browser after ~30s of inactivity.
When the SW dies:
- The TCP socket stays open at the OS/Chrome network stack level
- The relay's `self.browser` still points to the dead socket
- The relay's `_handle_browser` WS PING timeout takes up to 40s to detect the dead connection
- During that window: agent calls are forwarded into the void by the relay (write succeeds
  at socket level, nobody reads), agent waits for a response that never comes

**Critical missing piece**: `_call()` in `browserbox.py` used `async for raw in ws` with
NO read timeout. This hung indefinitely when extension was silently dead.

Note: the Chrome alarm (`periodInMinutes: 25/60`) is rounded up to 1 minute for unpacked
(developer mode) extensions — the alarm-only keepalive was never firing at 25s as intended.

### Fix: `browserbox.py` — receive deadline

Replaced `async for raw in ws` with a deadline-tracked loop:
```python
end = asyncio.get_event_loop().time() + recv_deadline
while True:
    remaining = end - asyncio.get_event_loop().time()
    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
```
Connect timeout (up to 3s) is now budgeted separately from the response wait.
Both `asyncio.TimeoutError` and `TimeoutError` are caught.

### Fix: `background.js` — SW keepalive via application ping

Added `startKeepalive()` — `setInterval` at 20s sends `{"type":"ping"}` over the WS.
An active `setInterval` is an active task; Chrome keeps the SW alive while it has work.
`stopKeepalive()` called on WS close. Called from `open` and `close` event handlers.

Chrome MV3 docs say open connections keep the SW alive, but in practice this is
unreliable for unpackaged extensions. The `setInterval` approach is more reliable
because it creates a concrete pending async task each tick.

### Fix: `ws_relay.py` — swallow keepalive pings

Both `_handle_browser` and `_handle_agent` now silently drop `{"type":"ping"/"pong"}`.
Without this, extension ping messages would be forwarded to agents as malformed
tool responses (no `id` field → agents would log warnings and ignore, but it was noise).

---

## What's next (potential)

- BrowserBox: test the fix with the extension loaded; verify callLog gets entries
- Operator TIR: test with a model that reliably uses the `python` tool
- Council: add "Add specialist" button to manually add cards after proposal
- Operator notes: UI surface for reading/editing `operator_notes.md` directly
