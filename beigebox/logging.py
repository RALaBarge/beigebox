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
from typing import Optional

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


