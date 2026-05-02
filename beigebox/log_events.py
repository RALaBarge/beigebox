"""Typed event envelopes for side-channel logging.

The chat-completion path has its own typed capture pipeline (see
``beigebox/capture.py`` — CaptureContext + CapturedRequest +
CapturedResponse). This module is the parallel for the ~9 side-channel
helpers that live in ``beigebox/logging.py``: routing decisions, tool
invocations, errors, anomalies, hook batches, request lifecycle markers,
and full-payload snapshots.

Design contract:

- Every event carries a :class:`LogEventContext` with identity + timing
  + source + severity. ``ts`` defaults to now() per instance.
- Each envelope (RequestLifecycleEvent / ToolExecutionEvent / ErrorEvent
  / RoutingEvent / PayloadEvent) builds the same kwargs dict the legacy
  ``wire.log(...)`` calls used, so the on-the-wire shape is unchanged.
  Callers in proxy.py / guardrails.py / backends/router.py keep their
  existing helper-function signatures.
- ``set_wire_log(w)`` is called once from main.py lifespan after the
  production WireLog is built. Until then, ``emit()`` is a no-op so
  early-bootstrap calls and isolated unit tests don't crash.
- No severity-based source-side filtering. Every event lands in every
  attached sink. The user's "capture everything" direction is enforced
  at this layer; sinks decide what to keep on disk.

Per the post-batch-B refactor: the 9 active helpers in logging.py
become thin wrappers around these envelopes; the 11 dead helpers
(orphaned by the v3 trim of operator/harness/judge/decision_llm/
z-commands/embedding_classifier) are deleted in commit A-5.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Union

logger = logging.getLogger(__name__)


# ── Shared identity + timing context ──────────────────────────────────────


Severity = Literal["debug", "info", "warn", "error", "critical"]


@dataclass
class LogEventContext:
    """Identity + timing + severity common to every log event.

    ``source`` names the subsystem that produced the event ("proxy",
    "router", "tools", "guardrails", "hooks", "extraction_detector",
    "rag_poisoning", …). It maps to the ``source`` field on the wire
    event row, used by Tap CLI filtering and downstream observability.

    ``ts`` is set per-instance via ``default_factory`` so every envelope
    carries a fresh UTC timestamp. Callers can override for replay /
    tests by passing ``ts=...`` explicitly.
    """

    source: str
    severity: Severity = "info"
    conv_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Five typed event categories ───────────────────────────────────────────


@dataclass
class RequestLifecycleEvent:
    """Proxy request lifecycle markers.

    Subsumes log_request_started + log_request_completed.
    Two distinct ``stage`` values; tokens / latency / cost are populated
    on completion only.
    """

    ctx: LogEventContext
    stage: Literal["started", "completed"]
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float | None = None
    cost_usd: float | None = None

    def to_wire_call(self) -> dict:
        meta: dict = {
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "cost": self.cost_usd,
            "severity": self.ctx.severity,
        }
        if self.stage == "started":
            content = f"Request start: {self.model} ({self.tokens_in} tokens)"
            direction = "inbound"
        else:
            cost_str = f"{self.cost_usd or 0:.6f}"
            content = (
                f"Request done: {self.latency_ms or 0:.0f}ms "
                f"({self.tokens_in}→{self.tokens_out} tokens) cost=${cost_str}"
            )
            direction = "outbound"
        return _wire_kwargs(
            direction=direction,
            role="request",
            content=content,
            event_type=f"request_{self.stage}",
            ctx=self.ctx,
            meta=meta,
        )


@dataclass
class ToolExecutionEvent:
    """Any tool invocation: regular tools, MCP tools, security MCP, CDP."""

    ctx: LogEventContext
    tool_name: str
    status: Literal["ok", "error", "timeout"]
    latency_ms: float | None = None
    error: str | None = None
    extra: dict | None = None

    def to_wire_call(self) -> dict:
        meta: dict = {
            "tool": self.tool_name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "severity": self.ctx.severity,
        }
        if self.extra:
            meta.update(self.extra)
        content = f"Tool {self.tool_name}: {self.status} ({self.latency_ms or 0:.0f}ms)"
        if self.error:
            content += f" error={self.error[:50]}"
        return _wire_kwargs(
            direction="inbound",
            role="tool",
            content=content,
            event_type="tool_call",
            ctx=self.ctx,
            meta=meta,
        )


@dataclass
class ErrorEvent:
    """Errors, security anomalies, extraction attempts. Severity-tagged."""

    ctx: LogEventContext
    component: str
    error_message: str
    risk_level: str | None = None
    confidence: float | None = None
    triggers: list[str] | None = None
    action: str | None = None
    extra: dict | None = None

    def to_wire_call(self) -> dict:
        meta: dict = {
            "component": self.component,
            "severity": self.ctx.severity,
            "error": self.error_message[:200],
        }
        if self.risk_level is not None:
            meta["risk_level"] = self.risk_level
        if self.confidence is not None:
            meta["confidence"] = self.confidence
        if self.triggers is not None:
            meta["triggers"] = self.triggers
        if self.action is not None:
            meta["action"] = self.action
        if self.extra:
            meta.update(self.extra)

        # Content + role + event_type derive from whether this is a plain
        # error or a security/extraction anomaly. Anomalies get richer
        # content lines so Tap CLI users can read them at a glance.
        if self.risk_level or self.triggers:
            triggers_str = ", ".join(self.triggers or []) or "none"
            content = (
                f"{self.component} risk [{self.risk_level or '?'}]: "
                f"{self.error_message[:100]} "
                f"(triggers={triggers_str}, "
                f"conf={self.confidence or 0:.2f})"
            )
            role = "security"
            event_type = (
                "extraction_attempt_detected"
                if self.component == "extraction_detector"
                else "security_anomaly"
            )
        else:
            content = f"ERROR {self.component}: {self.error_message[:100]}"
            role = "error"
            event_type = "error"
        return _wire_kwargs(
            direction="inbound",
            role=role,
            content=content,
            event_type=event_type,
            ctx=self.ctx,
            meta=meta,
            source_override=self.component,
        )


@dataclass
class RoutingEvent:
    """Backend selection / routing decisions."""

    ctx: LogEventContext
    backend: str
    model: str
    reason: str
    confidence: float | None = None
    extra: dict | None = None

    def to_wire_call(self) -> dict:
        meta: dict = {
            "backend": self.backend,
            "model": self.model,
            "reason": self.reason,
            "severity": self.ctx.severity,
        }
        if self.confidence is not None:
            meta["confidence"] = self.confidence
        if self.extra:
            meta.update(self.extra)
        content = f"Backend: {self.backend} ({self.model}) — {self.reason}"
        return _wire_kwargs(
            direction="inbound",
            role="backend",
            content=content,
            event_type="backend_selection",
            ctx=self.ctx,
            meta=meta,
        )


@dataclass
class PayloadEvent:
    """Full request/response payload snapshots — capture-everything path."""

    ctx: LogEventContext
    direction: Literal["inbound", "outbound", "internal"]
    model: str
    backend: str | None = None
    content: str = ""
    summary: dict | None = None
    tokens: int = 0
    cost_usd: float | None = None

    def to_wire_call(self) -> dict:
        meta: dict = {
            "backend": self.backend,
            "conversation_id": self.ctx.conv_id,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "severity": self.ctx.severity,
        }
        if self.summary:
            meta.update(self.summary)
        # Display content (truncation lives in WireLog.log; we hand the
        # full string through and let the sink-level rotation/limits
        # decide).
        return _wire_kwargs(
            direction=self.direction,
            role="payload",
            content=self.content,
            model=self.model,
            event_type="payload",
            ctx=self.ctx,
            meta=meta,
        )


@dataclass
class HookExecutionEvent:
    """HookManager batch execution (pre_request / post_response).

    Subsumes log_hook_execution. Always emits even when zero hooks ran
    so a missing event in Tap means the wrapper itself didn't run.
    """

    ctx: LogEventContext
    stage: Literal["pre_request", "post_response"]
    hook_names: list[str]
    total_latency_ms: float
    hook_errors: list[dict] = field(default_factory=list)

    def to_wire_call(self) -> dict:
        meta: dict = {
            "stage": self.stage,
            "hook_count": len(self.hook_names),
            "hook_names": list(self.hook_names),
            "total_latency_ms": self.total_latency_ms,
            "error_count": len(self.hook_errors),
            "errors": list(self.hook_errors),
            "severity": self.ctx.severity,
        }
        err_suffix = f" errors={len(self.hook_errors)}" if self.hook_errors else ""
        content = (
            f"Hooks {self.stage}: {len(self.hook_names)} ok "
            f"({self.total_latency_ms:.1f}ms){err_suffix}"
        )
        return _wire_kwargs(
            direction="internal",
            role="hook",
            content=content,
            event_type=f"hook_{self.stage}",
            ctx=self.ctx,
            meta=meta,
        )


# Union of all envelope types — used as the input to emit().
LogEvent = Union[
    RequestLifecycleEvent,
    ToolExecutionEvent,
    ErrorEvent,
    RoutingEvent,
    PayloadEvent,
    HookExecutionEvent,
]


# ── Internal helper: build the kwargs dict for WireLog.log() ──────────────


def _wire_kwargs(
    *,
    direction: str,
    role: str,
    content: str,
    event_type: str,
    ctx: LogEventContext,
    meta: dict,
    model: str = "",
    source_override: str | None = None,
) -> dict:
    """Translate an envelope into the kwargs ``WireLog.log()`` accepts.

    The on-the-wire shape stays identical to what the legacy helpers in
    logging.py used to produce — same fields, same names — so this is a
    drop-in replacement for the 9 wrappers.
    """
    kw: dict[str, Any] = {
        "direction": direction,
        "role": role,
        "content": content,
        "model": model,
        "event_type": event_type,
        "source": source_override or ctx.source,
        "meta": meta,
    }
    if ctx.conv_id:
        kw["conversation_id"] = ctx.conv_id
    if ctx.run_id:
        kw["run_id"] = ctx.run_id
    if ctx.turn_id:
        kw["turn_id"] = ctx.turn_id
    return kw


# ── Production WireLog injection + dispatch ──────────────────────────────


# Module-level singleton. main.py lifespan calls set_wire_log(proxy.wire)
# after the production WireLog is constructed (with all three sinks
# attached). Until then, emit() is a no-op so early-bootstrap calls and
# isolated unit tests don't crash.
_wire_log: Any = None


def set_wire_log(w: Any) -> None:
    """Bind the production WireLog so emit() can dispatch to it.

    Called once from the FastAPI lifespan startup. Tests can also call
    this with a fake WireLog (or None to reset between cases).
    """
    global _wire_log
    _wire_log = w


def get_wire_log() -> Any:
    """Return the bound WireLog, or None when set_wire_log hasn't fired yet."""
    return _wire_log


def emit(event: LogEvent) -> None:
    """Serialize a typed event to the bound WireLog.

    No-op when ``set_wire_log`` hasn't been called yet (early
    bootstrapping, isolated unit tests). Sink-level errors are
    swallowed inside WireLog.log; this function never raises.
    """
    if _wire_log is None:
        return
    try:
        kw = event.to_wire_call()
        _wire_log.log(**kw)
    except Exception as exc:
        logger.warning("emit() failed for %s: %s", type(event).__name__, exc)
