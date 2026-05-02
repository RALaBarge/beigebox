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
| Proxy request lifecycle (streaming) | ✅ Sealed | `proxy_stream_response` carries the same `NormalizedResponse.summary()` shape as non-streaming — finish_reason defaults to `"stop"` since stream completion is the only success path here. |
| Operator agentic loop | 🚮 Removed in v3 | The `operator_*` event family (`operator_start`, `_thought`, `_tool_call`, `_tool_result`, `_iteration_end`, `_nudge`, `_finish`) is no longer emitted — Operator was deleted. Old wirelog rows still classify via `replay.py` with a `legacy_*` prefix. |
| Decision agent routing tier | 🚮 Removed in v3 | `decision_llm_result` no longer emitted — decision LLM deleted. |
| Embedding classifier | 🚮 Removed in v3 | `classifier_result` / `embedding_decision` no longer emitted — classifier deleted. |
| Cache (hit/miss/store) | ✅ Sealed | `cache_hit`, `cache_miss`, `cache_store` with similarity + TTL. |
| Cost tracking | ✅ Sealed (non-stream) | `cost_tracking` per inference; streaming gap blocks attribution there. |
| Backend selection / failover | ✅ Sealed | `backend_selection` with reason; degraded fallback logged. |
| Guardrails (input/output blocking) | ✅ Sealed | `guardrail_block` event with rule + reason. |
| WASM transforms | ✅ Sealed | Wire event on transform completion (both stream + non-stream). |
| SQLite dual-write | ✅ Sealed | Every wire event lands in `wire_events` table. |
| Audit logger (security decisions) | ✅ Sealed | Separate `audit.db`, not on the Tap bus. |
| Payload.jsonl | ✅ Sealed | Full request/response capture, gated by `payload_log_enabled`. |
| MCP `/mcp` tool calls | ✅ Sealed | Per-call `tool_call` wire event from `_tools_call` (try/finally guarantees exactly one emit per call); `source="mcp"` and `extra_meta` carry server label + input_length. |
| MCP `/pen-mcp` tool calls (security_mcp) | ✅ Sealed | Same dispatch as `/mcp`; tagged `source="pen-mcp"` so events are distinguishable in Tap. |
| Auth (key validation, ACL, rate limit) | ✅ Sealed | `auth_denied` wire event before every 401/403/429 return; meta carries reason_code, principal, endpoint, client_ip, user_agent. |
| Hooks (pre/post-request HookManager) | ✅ Sealed | `hook_pre_request` / `hook_post_response` events with hook_names (successful runs), errors (per-hook with name + truncated msg), total_latency_ms. Framework crash captured under `__framework_crash__` sentinel. |
| Z-commands | ✅ Sealed | `z_command_received` (with branch=help/fork/tools/route/model/noop), `z_command_executed` / `z_command_error` around forced-tool dispatch. |
| Extraction detector | ✅ Sealed | `extraction_attempt_detected` fires from `_run_request_pipeline` for HIGH/CRITICAL scores; LOW/MEDIUM stay off the bus. |
| Injection guard / RAG poisoning | ✅ Sealed | All three vector backends (memory/chroma/postgres) emit `security_anomaly` with detector_type, action, confidence, reason, vector_id, backend before the warn/quarantine/strict branch. |
| CDP browser actions (navigate, click, screenshot, …) | ✅ Sealed | `cdp.run()` wraps every dispatch in try/finally and emits one `tool_call` event with source="cdp", action, latency_ms, status, input_chars, result_chars/result_kind. |
| AMF mesh (advertise / discover / heartbeat) | ✅ Sealed | `amf_advertise` / `amf_heartbeat` / `amf_unregister` per transport (mdns / nats), with status (ok/skipped/error), agent_id, endpoint, error. |

---

## Canonical event sources

Every helper that ends up on the Tap bus or in a structured store. If you're
adding a new emit site, prefer extending one of these rather than introducing a
new code path.

All side-channel logging now flows through typed event envelopes
(`beigebox/log_events.py`). The 9 thin wrappers in `beigebox/logging.py`
build the appropriate envelope and dispatch through `emit()` to the
production WireLog (set via `set_wire_log` at lifespan startup). WireLog
fans out to three sinks: JSONL (`./data/wire.jsonl`), SQLite
`wire_events` (via WireEventRepo), and Postgres `wire_events` (when
`storage.postgres.connection_string` is set).

| Function | Envelope | Event type(s) | Captures | Sinks |
|---|---|---|---|---|
| `logging.log_payload_event` | `PayloadEvent` | `payload` | source, model, backend, latency_ms, **`extra_meta`** (NormalizedRequest/Response.summary) — full body also written to `payload.jsonl` (separate concern) | JSONL + SQLite + Postgres + `payload.jsonl` |
| `logging.log_request_started` / `_completed` | `RequestLifecycleEvent` | `request_started`, `request_completed` | model, tokens_in, tokens_out, latency, cost | JSONL + SQLite + Postgres |
| `logging.log_backend_selection` | `RoutingEvent` | `backend_selection` | backend, model, reason | JSONL + SQLite + Postgres |
| `logging.log_tool_call` | `ToolExecutionEvent` | `tool_call` | tool, status, latency_ms, error, source (tools / mcp / pen-mcp / cdp), extra | JSONL + SQLite + Postgres |
| `logging.log_error_event` | `ErrorEvent` | `error` | component, severity, message | JSONL + SQLite + Postgres |
| `logging.log_extraction_attempt` | `ErrorEvent` | `extraction_attempt_detected` | session_id, risk_level, confidence, triggers, reason | JSONL + SQLite + Postgres *(emitted from `Proxy._run_request_pipeline` for HIGH/CRITICAL)* |
| `logging.log_hook_execution` | `HookExecutionEvent` | `hook_pre_request`, `hook_post_response` | stage, hook_names (success), errors (failures), total_latency_ms | JSONL + SQLite + Postgres |
| `logging.log_security_anomaly` | `ErrorEvent` | `security_anomaly` | detector_type, action, confidence, reason, *(per-detector extras)* | JSONL + SQLite + Postgres |
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

## Recently closed (kept for traceability)

- **Streaming response normalizer summary** — closed in `d29bfd6`. `proxy_stream_response` now carries the same `.summary()` shape as non-streaming.
- **Auth denial wire events** — closed in `d29bfd6`. `auth_denied` event fires before every 401/403/429 with reason_code + principal + endpoint + client_ip + user_agent.
- **MCP `/mcp` + `/pen-mcp` per-call events** — closed in `d29bfd6`. `_tools_call` wraps every dispatch with try/finally so every call emits exactly one `tool_call` event tagged with `source="mcp"` or `source="pen-mcp"`.
- **HookManager execution observability** — closed post-`34954a1`. `run_pre_request_with_meta` / `run_post_response_with_meta` return per-batch metadata; the proxy emits `hook_pre_request` / `hook_post_response` events with hook_count, hook_names, latency, and per-hook errors. Framework crash is also captured via outer try/except.
- **Z-command receipt + execute events** — closed post-`34954a1`. `_process_z_command` emits `z_command_received` with branch (help/fork/tools/route/model/noop); `_run_forced_tools` emits `z_command_executed` or `z_command_error` after dispatch. Parser-crash path also instrumented.
- **CDP action events** — closed post-`34954a1`. `CDPTool.run()` wraps every dispatch in try/finally and emits one `tool_call` event with source="cdp", action, status, latency_ms, input_chars, result_chars (or result_kind="image_envelope" for screenshots). Status is derived from `Error:`-prefixed return strings.
- **RAG poisoning events** — closed post-`34954a1`. All three vector backends (`memory.py`, `chroma.py`, `postgres.py`) emit `security_anomaly` (detector_type=rag_poisoning, action=warn/quarantine/strict, confidence, reason, vector_id, backend) before applying the configured action. Event lands even when `strict` mode raises.
- **AMF mesh observability** — closed post-`34954a1`. `_emit_event` helper centralises `log_amf_event` calls; `amf_advertise` / `amf_heartbeat` / `amf_unregister` cover both transports (mdns + nats) with status (ok/skipped/error), agent_id, endpoint.
- **Extraction-attempt wire-up** — closed post-`34954a1`. `Proxy._run_request_pipeline` calls `extraction_detector.check_request()` after the input guardrail and emits `extraction_attempt_detected` (a) for HIGH/CRITICAL always, (b) for MEDIUM during the per-session baseline window only (`baseline_established == False`). Past baseline, MEDIUM stays off the bus to keep noise down.

## Open gaps (in priority order)

No P1/P2 observability gaps remain in the surveyed surfaces. Re-audit when a new
HTTP endpoint, agent loop, or external integration is added (see *Last reviewed*
trigger list below). The next likely additions to revisit:

1. **Per-hook latency** *(LOW — currently only batch latency is captured)*
   - `HookManager.run_pre_request_with_meta` already times each hook internally but only surfaces batch totals. If a single hook degrades silently, only the batch grows. Promote per-hook timings into the wire event when noise budget allows.
2. **Streaming wire-event correlation IDs** *(LOW — Grok suggestion deferred from `34954a1`)*
   - MCP `tool_call` events don't carry a request_id/trace_id, so correlating across the wire still requires conversation_id + timestamp triangulation.

---

## Last reviewed

2026-04-26 (post-`34954a1` + this commit). Re-run the audit when:
- A new HTTP endpoint is added
- A new agent loop / orchestrator is added
- A new external API integration is added
- A new SQLite/blob store is added
- Quarterly, regardless

The full audit can be regenerated with the Explore agent — see commit `8f783d3` for the prompt that produced this report.
