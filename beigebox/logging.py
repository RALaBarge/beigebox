"""Backwards-compatible thin wrappers around beigebox.log_events.

Each of the 9 functions below takes the same signature it always had so
existing call sites in proxy.py / guardrails.py / backends/router.py /
tools/registry.py / etc. don't change. Internally each builds a typed
event envelope (RequestLifecycleEvent, ToolExecutionEvent, ErrorEvent,
RoutingEvent, PayloadEvent, HookExecutionEvent) and dispatches via
``log_events.emit()`` — which fans out through WireLog to the three
sinks (JSONL, SQLite, Postgres) attached at lifespan time.

11 dead helpers (orphaned by the v3 trim of operator/harness/judge/
decision_llm/z-commands/embedding_classifier) are deleted in commit A-5
after a tree-wide grep verifies zero callers.

Per user direction ("capture everything I can in logging"), the
payload_log_enabled runtime toggle is removed from log_payload_event:
every payload event always lands in every sink. Disk pressure is
managed at the sink level (JSONL rotation; postgres/sqlite size-bound
retention if/when added).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from beigebox.log_events import (
    ErrorEvent,
    HookExecutionEvent,
    LogEventContext,
    PayloadEvent,
    RequestLifecycleEvent,
    RoutingEvent,
    ToolExecutionEvent,
    emit,
)

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Active helpers (9). Each is a thin wrapper; signatures are unchanged.
# ───────────────────────────────────────────────────────────────────────────


def log_request_started(model: str, tokens: int) -> None:
    """Log the start of a request."""
    emit(RequestLifecycleEvent(
        ctx=LogEventContext(source="proxy"),
        stage="started",
        model=model,
        tokens_in=tokens,
    ))


def log_request_completed(
    model: str,
    latency_ms: float,
    tokens_in: int,
    tokens_out: int,
    cost: Optional[float] = None,
) -> None:
    """Log request completion with full stats."""
    emit(RequestLifecycleEvent(
        ctx=LogEventContext(source="proxy"),
        stage="completed",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        cost_usd=cost,
    ))


def log_backend_selection(backend: str, model: str, reason: str) -> None:
    """Log which backend was selected and why."""
    emit(RoutingEvent(
        ctx=LogEventContext(source="router"),
        backend=backend,
        model=model,
        reason=reason,
    ))


def log_tool_call(
    tool_name: str,
    status: str,
    latency_ms: float,
    error: Optional[str] = None,
    source: str = "tools",
    extra_meta: Optional[dict] = None,
) -> None:
    """Log tool invocation and result.

    ``source`` distinguishes the calling subsystem ("tools" for the regular
    tool registry, "mcp" for /mcp tool calls, "pen-mcp" for the security
    MCP, "cdp" for browser automation). ``extra_meta`` is merged into the
    event meta dict for per-source fields.
    """
    emit(ToolExecutionEvent(
        ctx=LogEventContext(source=source),
        tool_name=tool_name,
        # Status string preserved as-is — callers pass "ok", "error",
        # "timeout", or anything else; the typed Literal is permissive
        # at the wrapper boundary so existing call sites don't break.
        status=status,  # type: ignore[arg-type]
        latency_ms=latency_ms,
        error=error,
        extra=extra_meta,
    ))


def log_error_event(component: str, error: str, severity: str = "error") -> None:
    """Log errors and exceptions."""
    emit(ErrorEvent(
        ctx=LogEventContext(source=component, severity=severity),  # type: ignore[arg-type]
        component=component,
        error_message=error,
    ))


def log_extraction_attempt(
    session_id: str,
    risk_level: str,
    confidence: float,
    triggers: list[str],
    reason: str,
) -> None:
    """Log model extraction attack detection event.

    ``session_id`` is recorded in extra so legacy consumers that read
    ``meta.session_id`` keep working.
    """
    emit(ErrorEvent(
        ctx=LogEventContext(source="extraction_detector", severity="warn"),
        component="extraction_detector",
        error_message=reason,
        risk_level=risk_level,
        confidence=confidence,
        triggers=list(triggers),
        extra={"session_id": session_id},
    ))


def log_hook_execution(
    stage: str,
    hook_names: list[str],
    total_latency_ms: float,
    hook_errors: list[dict] | None = None,
    conversation_id: str = "",
) -> None:
    """Log a HookManager batch execution.

    Always emits — even when zero hooks ran — so a missing event in Tap
    means the wrapper itself didn't run.
    """
    emit(HookExecutionEvent(
        ctx=LogEventContext(source="hooks", conv_id=conversation_id or None),
        stage=stage,  # type: ignore[arg-type]
        hook_names=list(hook_names),
        total_latency_ms=total_latency_ms,
        hook_errors=list(hook_errors or []),
    ))


def log_payload_event(
    source: str,
    payload: dict | None = None,
    response: str | None = None,
    model: str = "",
    backend: str = "",
    conversation_id: str = "",
    latency_ms: float = 0.0,
    extra_meta: dict | None = None,
) -> None:
    """Log a full LLM payload — bus summary + payload.jsonl full body.

    No runtime gate (capture-everything direction). Every call fires:
    - PayloadEvent dispatched through the typed-event path (lands in
      JSONL + SQLite + Postgres sinks).
    - Full body written to payload.jsonl via beigebox.payload_log.

    ``extra_meta`` (typically NormalizedRequest.summary() or
    NormalizedResponse.summary()) is merged into the event's summary
    dict so call sites don't have to re-extract normalizer fields
    (transforms, errors, usage, finish_reason, …) at every site.
    """
    msg_count = len(payload.get("messages", [])) if payload else 0
    summary_parts = [f"Payload {source}: {model}"]
    if msg_count:
        summary_parts.append(f"[{msg_count} msgs]")
    if latency_ms:
        summary_parts.append(f"({latency_ms:.0f}ms)")
    summary_line = " ".join(summary_parts)

    emit(PayloadEvent(
        ctx=LogEventContext(source=source, conv_id=conversation_id or None),
        direction="outbound" if payload else "inbound",
        model=model,
        backend=backend or None,
        content=summary_line,
        summary=dict(extra_meta) if extra_meta else None,
    ))

    # Full body written to payload.jsonl — off the bus, separate concern.
    # Wrapped in try/except because payload.jsonl is best-effort: a
    # broken file write must not break the wire path.
    try:
        from beigebox.payload_log import write_payload
        write_payload(
            source=source,
            payload=payload,
            response=response,
            model=model,
            backend=backend,
            conversation_id=conversation_id,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        logger.debug("write_payload failed: %s", exc)


def log_security_anomaly(
    detector_type: str,
    action: str,
    confidence: float,
    reason: str,
    extra: dict | None = None,
) -> None:
    """Log a security-detector anomaly (poisoned embedding, injection, etc.)."""
    emit(ErrorEvent(
        ctx=LogEventContext(source=detector_type, severity="warn"),
        component=detector_type,
        error_message=reason,
        confidence=confidence,
        action=action,
        extra=extra,
    ))


# ───────────────────────────────────────────────────────────────────────────
# Dead helpers (11). Pending deletion in commit A-5 after a tree-wide grep
# confirms zero non-self callers. Kept here in this commit so A-4 is purely
# the typed-envelope swap with no behaviour change for unused code.
# ───────────────────────────────────────────────────────────────────────────


def _get_tap_logger():
    """Legacy WireLog accessor — still used by the dead helpers below.

    Active helpers above route through log_events.emit() instead, which
    uses the production WireLog set via log_events.set_wire_log() in
    main.py lifespan. This shim is removed when the dead helpers are
    deleted (commit A-5).
    """
    try:
        from beigebox.main import get_state
        state = get_state()
        return state.proxy.wire if state.proxy else None
    except Exception:
        return None


def log_routing_decision(
    decision: str,
    route: str,
    confidence: float,
    latency_ms: float,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """[DEAD] Log routing tier decision. Removed in v3; pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {
        "decision": decision, "route": route, "confidence": confidence,
        "latency_ms": latency_ms, **(details or {}),
    }
    content = f"Route: {decision} → {route} (confidence={confidence:.3f}, {latency_ms:.1f}ms)"
    wire.log(
        direction="inbound", role="router", content=content,
        event_type="routing_decision", source="router", meta=meta,
    )


def log_model_selection(context: str, model: str, reason: str) -> None:
    """[DEAD] Log model selection decision. Removed in v3; pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {"context": context, "model": model, "reason": reason}
    content = f"Model {context}: {model} ({reason})"
    wire.log(
        direction="inbound", role="model_selector", content=content,
        event_type="model_selection", source="router", meta=meta,
    )


def log_token_usage(
    component: str, model: str, prompt_tokens: int,
    completion_tokens: int, total_tokens: int, cost: Optional[float] = None,
) -> None:
    """[DEAD] Log token usage per component. Pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {
        "component": component, "model": model,
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "total_tokens": total_tokens, "cost": cost,
    }
    content = f"Tokens {component}: {total_tokens} (p={prompt_tokens}, c={completion_tokens}) cost=${cost or 0:.4f}"
    wire.log(
        direction="inbound", role="token_counter", content=content,
        event_type="token_usage", source="inference", meta=meta,
    )


def log_latency_stage(
    stage: str, latency_ms: float, details: Optional[Dict[str, Any]] = None,
) -> None:
    """[DEAD] Log latency for a specific stage. Pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {"stage": stage, "latency_ms": latency_ms, **(details or {})}
    content = f"Latency {stage}: {latency_ms:.1f}ms"
    wire.log(
        direction="inbound", role="profiler", content=content,
        event_type="latency_stage", source="profiler", meta=meta,
    )


def log_judge_scores(
    component: str, scores: Dict[str, float], weighted: Optional[float] = None,
) -> None:
    """[DEAD] Log LLM judge scoring results. Removed in v3; pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {"component": component, "scores": scores, "weighted": weighted}
    score_str = ", ".join(f"{k}={v:.2f}" for k, v in scores.items())
    content = f"Judge {component}: {score_str}"
    if weighted is not None:
        content += f" weighted={weighted:.3f}"
    wire.log(
        direction="inbound", role="judge", content=content,
        event_type="judge_score", source="judge", meta=meta,
    )


def log_cost_event(source: str, model: str, cost: float, tokens: int) -> None:
    """[DEAD] Log cost tracking event. Pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {
        "source": source, "model": model, "cost": cost, "tokens": tokens,
        "cost_per_token": cost / tokens if tokens > 0 else 0,
    }
    content = f"Cost {source}: ${cost:.6f} ({tokens} tokens)"
    wire.log(
        direction="inbound", role="cost_tracker", content=content,
        event_type="cost_tracking", source="billing", meta=meta,
    )


def log_embedding_decision(
    similarity: float, threshold: float, decision: str,
) -> None:
    """[DEAD] Log embedding classifier decision. Removed in v3; pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {"similarity": similarity, "threshold": threshold, "decision": decision}
    content = f"Embedding: similarity={similarity:.3f} vs threshold={threshold:.3f} → {decision}"
    wire.log(
        direction="inbound", role="classifier", content=content,
        event_type="embedding_decision", source="classifier", meta=meta,
    )


def log_classifier_run(scores: dict, chosen_route: str, confidence: float) -> None:
    """[DEAD] Log embedding classifier results. Removed in v3; pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {"scores": scores, "chosen_route": chosen_route, "confidence": confidence}
    score_str = ", ".join(f"{k}={v:.2f}" for k, v in scores.items())
    content = f"Classifier: {chosen_route} (conf={confidence:.3f}) scores=[{score_str}]"
    wire.log(
        direction="inbound", role="classifier", content=content,
        event_type="classifier_result", source="classifier", meta=meta,
    )


def log_decision_llm_call(
    prompt_len: int, decision: str, confidence: float, latency_ms: float,
) -> None:
    """[DEAD] Log decision LLM judge call. Removed in v3; pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {
        "prompt_len": prompt_len, "decision": decision,
        "confidence": confidence, "latency_ms": latency_ms,
    }
    content = f"Judge: {decision} (conf={confidence:.3f}, {latency_ms:.0f}ms)"
    wire.log(
        direction="inbound", role="judge", content=content,
        event_type="decision_llm_result", source="judge", meta=meta,
    )


def log_harness_turn(
    run_id: str, turn: int, model: str,
    tokens_in: int, tokens_out: int, status: str,
) -> None:
    """[DEAD] Log harness/orchestrator turn. Removed in v3; pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {
        "run_id": run_id, "turn": turn, "model": model,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "status": status,
    }
    content = f"Harness turn {turn}: {status} ({tokens_in}→{tokens_out} tokens)"
    wire.log(
        direction="inbound", role="harness", content=content,
        event_type="harness_turn", source="harness", run_id=run_id, meta=meta,
    )


def log_z_command(
    status: str, directives: str = "", route: str = "", model: str = "",
    tools: list[str] | None = None, message_len: int = 0, branch: str = "",
    error: str | None = None, conversation_id: str = "",
) -> None:
    """[DEAD] Log z-command lifecycle. Removed in v3; pending deletion."""
    wire = _get_tap_logger()
    if not wire:
        return
    meta = {
        "status": status, "directives": directives, "route": route,
        "model": model, "tools": tools or [], "message_len": message_len,
        "branch": branch, "error": error,
    }
    suffix = f" branch={branch}" if branch else ""
    if error:
        suffix += f" error={error[:80]}"
    content = f"z-command {status}: directives={directives or '(none)'}{suffix}"
    wire.log(
        direction="internal", role="decision", content=content, model="z-command",
        conversation_id=conversation_id, event_type=f"z_command_{status}",
        source="z_command", meta=meta,
    )
