# Observability Coverage & Rubric

Definitive map of what BeigeBox emits to the Tap event log today, where the
gaps are, and the decision rules for when a new feature must add its own
observability.

For *how to query* the events listed here, see [observability.md](observability.md).

---

## Coverage matrix

| Subsystem | State | Notes |
|---|---|---|
| Model I/O — request/response normalizers + payload logging | ✅ Sealed | Request and response normalizers expose `.summary(context)`; `log_payload_event(extra_meta=...)` carries it onto Tap. |
| Proxy request lifecycle (non-streaming) | ✅ Sealed | Auth → routing → normalize → backend → response all emit. |
| Proxy request lifecycle (streaming) | 🟡 Partial | `proxy_stream_response` event missing `NormalizedResponse.summary()` — no finish_reason / usage / tool_calls count. |
| Operator agentic loop | ✅ Sealed | `operator_start`, `operator_thought`, `operator_tool_call`, `operator_tool_result`, `operator_iteration_end`, `operator_nudge`, `operator_finish`. |
| Decision agent routing tier | ✅ Sealed | `decision_llm_result` (prompt_len, decision, confidence, latency). |
| Embedding classifier | ✅ Sealed | `classifier_result`, `embedding_decision`. |
| Cache (hit/miss/store) | ✅ Sealed | `cache_hit`, `cache_miss`, `cache_store` with similarity + TTL. |
| Cost tracking | ✅ Sealed (non-stream) | `cost_tracking` per inference; streaming gap blocks attribution there. |
| Backend selection / failover | ✅ Sealed | `backend_selection` with reason; degraded fallback logged. |
| Guardrails (input/output blocking) | ✅ Sealed | `guardrail_block` event with rule + reason. |
| WASM transforms | ✅ Sealed | Wire event on transform completion (both stream + non-stream). |
| SQLite dual-write | ✅ Sealed | Every wire event lands in `wire_events` table. |
| Audit logger (security decisions) | ✅ Sealed | Separate `audit.db`, not on the Tap bus. |
| Payload.jsonl | ✅ Sealed | Full request/response capture, gated by `payload_log_enabled`. |
| MCP `/mcp` tool calls | 🟡 Partial | No per-call wire event; tool calls flow through operator (which logs) but `_tools_call` emits nothing of its own. |
| MCP `/pen-mcp` tool calls (security_mcp) | 🔴 Gap | Entire endpoint has no observability tier. Wrappers run unmonitored. |
| Auth (key validation, ACL, rate limit) | 🟡 Partial | 401/403/429 returned correctly but **no wire event** — silent denials. |
| Hooks (pre/post-request HookManager) | 🔴 Gap | Execution and errors silent. |
| Z-commands | 🔴 Gap | No events on receipt / parse / execute. |
| Extraction detector | 🟡 Partial | `log_extraction_attempt()` defined in `logging.py` but **never called**. |
| Injection guard / RAG poisoning | 🟡 Partial | Detection updates quarantine but emits no wire event. |
| CDP browser actions (navigate, click, screenshot, …) | 🟡 Partial | stdlib logger only — no wire events. |
| AMF mesh (advertise / discover / heartbeat) | 🔴 Gap | No observability tier visible. |

---

## Canonical event sources

Every helper that ends up on the Tap bus or in a structured store. If you're
adding a new emit site, prefer extending one of these rather than introducing a
new code path.

| Function | Event type(s) | Captures | Sink |
|---|---|---|---|
| `logging.log_payload_event` | `payload` | source (proxy / proxy_response / proxy_stream / proxy_stream_response / operator…), model, backend, latency_ms, **`extra_meta`** (NormalizedRequest/Response.summary) | Tap + `payload.jsonl` |
| `logging.log_request_started` / `_completed` | `request_started`, `request_completed` | model, tokens_in, tokens_out, latency, cost | Tap |
| `logging.log_routing_decision` | `routing_decision` | tier, route, confidence, latency_ms | Tap |
| `logging.log_backend_selection` | `backend_selection` | backend, model, reason | Tap |
| `logging.log_decision_llm_call` | `decision_llm_result` | prompt_len, decision, confidence, latency_ms | Tap |
| `logging.log_classifier_run` | `classifier_result` | scores, chosen_route, confidence | Tap |
| `logging.log_embedding_decision` | `embedding_decision` | similarity, threshold, decision | Tap |
| `logging.log_cache_event` | `cache_hit`, `cache_miss`, `cache_store` | cache_type, key_hash, similarity, ttl | Tap |
| `logging.log_cost_event` | `cost_tracking` | source, model, cost, tokens, cost_per_token | Tap |
| `logging.log_token_usage` | `token_usage` | component, model, prompt/completion/total tokens, cost | Tap |
| `logging.log_latency_stage` | `latency_stage` | stage, latency_ms, details | Tap |
| `logging.log_judge_scores` | `judge_score` | component, scores, weighted | Tap |
| `logging.log_model_selection` | `model_selection` | context, model, reason | Tap |
| `logging.log_tool_call` | `tool_call` | tool, status, latency_ms, error | Tap |
| `logging.log_harness_turn` | `harness_turn` | run_id, turn, model, tokens, status | Tap |
| `logging.log_error_event` | `error` | component, severity, message | Tap |
| `logging.log_extraction_attempt` | `extraction_attempt_detected` | session_id, risk_level, confidence, triggers | Tap *(defined, not called — see gap below)* |
| `proxy.wire.log` | varies (`cache_hit`, `wasm`, `validation_warn`, `guardrail_block`, …) | model, conversation_id, content, meta | Tap + SQLite |
| `proxy._log_messages` / `_log_response` | (no event_type) | conversation_id, role, content, model, cost, latency | Tap + SQLite |
| `operator._wire(event_type, …)` | `operator_*` family | run_id, content, model, tokens, elapsed_ms, meta | Tap + SQLite |
| `harness_orchestrator._wire` | `harness_turn` | event_type, source=harness, run_id, turn_id, meta | Tap + SQLite |
| `WireLog.log` → `log_wire_event` | (whatever event_type is set) | event_type, source, content, role, model, conv_id, run_id, turn_id, tool_id, meta | Tap + SQLite |
| `security/audit_logger.log_validation` | (audit DB, not Tap) | tool, action, input_hash, decision, reason, severity, bypass_attempt | `audit.db` |
| `payload_log.write_payload` | (payload.jsonl) | source, payload, response, model, backend, conv_id, latency_ms | `payload.jsonl` (gated) |

---

## Decision rubric — when to add observability

Apply these rules before merging any new feature. If the new code path matches
any rule and **doesn't** emit, add observability before you ship.

1. **Silent failure path.** A function returns degraded / empty / `None` without raising → emit a wire event with the outcome and fallback reason. (Cache miss fallback, classifier tie-break, guardrail redaction.)
2. **External network call.** Any call to a remote API (backend, embedding, MCP tool) → emit a wire event with target, status, latency_ms, and cost (if billable).
3. **Paid resource consumption.** Any code that incurs token cost or per-call billing → emit `cost_tracking` (or include cost in the normalizer summary). Trigger: every inference, every paid external API.
4. **Routing / selection decision.** Choosing among ≥2 alternatives (backend, model, tool, prompt, cache vs. fresh) → emit decision + alternatives + confidence + reason.
5. **Request-side state transform.** A normalizer rule, hook, or middleware mutates the incoming request (rewrites messages, injects system prompt, redacts PII) → name the transform in the request normalizer summary; emit an `error` event if it can fail/lose data.
6. **Response-side anomaly or degrade.** Response missing expected fields, malformed, or triggering a fallback (unicode sanitization, tool_calls coercion) → emit error event with severity + field; include errors list in response normalizer summary.
7. **Auth / access-control denial.** Any deny decision (invalid key, ACL block, rate limit) → emit a wire event with principal + resource + reason. **Denials must never be silent.**
8. **Agentic loop state transition.** Every step of an agent loop (thought → tool_call → tool_result → iteration_end) → emit event with state, latency, token estimate, error reason on failure.
9. **Resource budget exhaustion.** Token budget, tool count, loop depth, or request size exceeds a threshold → emit warning with current_value, limit, action.
10. **New HTTP endpoint.** Every new `@app.post/get/...` handler MUST emit ≥1 wire event per request. Zero-emission endpoints are invisible to observability and either need a wire event or an explicit code comment justifying why not. *(This rule alone would have caught the `/pen-mcp` gap.)*

---

## Open gaps (in priority order)

Each is paired with the file:line where the emit should land and the event_type to use. Re-rank as your incident history evolves.

1. **Streaming response normalizer summary** *(HIGH — blocks streaming cost attribution)*
   - `proxy.py:~1787` — assemble `normalize_response({"choices": [...]}).summary(...)` after stream finalization, pass via `extra_meta=...` to `log_payload_event("proxy_stream_response", ...)`.
2. **Auth denial wire events** *(HIGH — required for breach forensics, rate-limit tuning)*
   - `main.py:685-722` — emit `auth_denied` (subtype: `invalid_key`, `rate_limit_exceeded`, `endpoint_not_allowed`) before each 401/403/429 return. Fields: principal_name, principal_type, endpoint_path, reason_code.
3. **MCP `/mcp` and `/pen-mcp` tool invocation events** *(HIGH for `/pen-mcp` — currently invisible)*
   - `mcp_server.py:_tools_call` — emit `mcp_tool_call` before/after tool execution. Fields: server (`mcp` or `pen-mcp`), tool_name, input_length, status, latency_ms, error.
4. **Hook execution timing + errors** *(MEDIUM — silent post-processing)*
   - Wrap `HookManager.run_pre_request()` / `run_post_response()` callsites in proxy.py. Event: `hook_pre_request`, `hook_post_response`. Fields: hook_count, hook_names, total_latency_ms, hook_errors.
5. **Z-command receipt / execute** *(MEDIUM — forensics, exploit detection)*
   - Locate Z-command parser. Events: `z_command_received`, `z_command_executed`, `z_command_error`. Fields: command_name, arguments_hash (not raw — security), latency_ms, status.
6. **CDP action wire events** *(MEDIUM — browser automation debugging)*
   - `tools/cdp.py` — `_screenshot`, `_navigate`, `_click`, etc. Event: `cdp_action` with action subtype. Fields: action_type, latency_ms, status, error, result_size_kb (screenshot).
7. **Injection guard / RAG poisoning detection events** *(MEDIUM — security calibration)*
   - `proxy.py` injection_guard callsites + `storage/backends/memory.py:82`. Event: `security_anomaly`. Fields: detector_type, pattern, input_hash, confidence, action.

### Side findings worth noting

- `log_extraction_attempt()` is defined in `beigebox/logging.py` but never called. Either wire it up or delete it (lest someone assume the detector is observable when it isn't).
- The Pen/Sec MCP at `/pen-mcp` is the cleanest example of how easy it is to ship endpoints without observability. Rule #10 above exists specifically to prevent that recurrence.

---

## Last reviewed

2026-04-25 (post-`8f783d3`). Re-run the audit when:
- A new HTTP endpoint is added
- A new agent loop / orchestrator is added
- A new external API integration is added
- A new SQLite/blob store is added
- Quarterly, regardless

The full audit can be regenerated with the Explore agent — see commit `8f783d3` for the prompt that produced this report.
