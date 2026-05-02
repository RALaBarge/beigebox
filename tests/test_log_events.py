"""Unit tests for beigebox.log_events.

Pure-function tests against the typed envelopes and the emit() dispatch.
No DB, no real WireLog, no I/O.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from beigebox.log_events import (
    ErrorEvent,
    HookExecutionEvent,
    LogEventContext,
    PayloadEvent,
    RequestLifecycleEvent,
    RoutingEvent,
    ToolExecutionEvent,
    emit,
    get_wire_log,
    set_wire_log,
)


def _ctx(**overrides) -> LogEventContext:
    base = dict(source="proxy", severity="info", conv_id="c1")
    base.update(overrides)
    return LogEventContext(**base)


# ── LogEventContext --------------------------------------------------------


class TestLogEventContext:
    def test_ts_auto_populated(self):
        c = LogEventContext(source="proxy")
        assert isinstance(c.ts, datetime)
        assert c.ts.tzinfo is not None  # tz-aware

    def test_ts_distinct_per_instance(self):
        c1 = LogEventContext(source="proxy")
        c2 = LogEventContext(source="proxy")
        # Two separate field(default_factory=...) calls must yield two
        # distinct datetime instances (or at least not be aliased).
        assert c1 is not c2

    def test_explicit_ts_preserved(self):
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        c = LogEventContext(source="proxy", ts=ts)
        assert c.ts == ts

    def test_default_severity_is_info(self):
        c = LogEventContext(source="proxy")
        assert c.severity == "info"


# ── RequestLifecycleEvent --------------------------------------------------


class TestRequestLifecycleEvent:
    def test_started_stage_emits_inbound(self):
        ev = RequestLifecycleEvent(
            ctx=_ctx(),
            stage="started",
            model="gpt-4o",
            tokens_in=10,
        )
        kw = ev.to_wire_call()
        assert kw["direction"] == "inbound"
        assert kw["event_type"] == "request_started"
        assert kw["role"] == "request"
        assert "Request start" in kw["content"]
        assert kw["meta"]["model"] == "gpt-4o"
        assert kw["meta"]["tokens_in"] == 10

    def test_completed_stage_emits_outbound_with_stats(self):
        ev = RequestLifecycleEvent(
            ctx=_ctx(),
            stage="completed",
            model="gpt-4o",
            tokens_in=10,
            tokens_out=20,
            latency_ms=420.5,
            cost_usd=0.0042,
        )
        kw = ev.to_wire_call()
        assert kw["direction"] == "outbound"
        assert kw["event_type"] == "request_completed"
        assert "420ms" in kw["content"]
        assert kw["meta"]["latency_ms"] == 420.5
        assert kw["meta"]["cost"] == 0.0042

    def test_severity_propagates_into_meta(self):
        ev = RequestLifecycleEvent(
            ctx=_ctx(severity="warn"), stage="started", model="m",
        )
        assert ev.to_wire_call()["meta"]["severity"] == "warn"


# ── ToolExecutionEvent -----------------------------------------------------


class TestToolExecutionEvent:
    def test_ok_invocation(self):
        ev = ToolExecutionEvent(
            ctx=_ctx(source="tools"),
            tool_name="web_search",
            status="ok",
            latency_ms=120.0,
        )
        kw = ev.to_wire_call()
        assert kw["event_type"] == "tool_call"
        assert kw["role"] == "tool"
        assert kw["source"] == "tools"
        assert kw["meta"]["status"] == "ok"
        assert "web_search" in kw["content"]

    def test_error_invocation_includes_error_in_content(self):
        ev = ToolExecutionEvent(
            ctx=_ctx(source="mcp"),
            tool_name="run_query",
            status="error",
            latency_ms=80.0,
            error="connection refused",
        )
        kw = ev.to_wire_call()
        assert "error=connection refused" in kw["content"]
        assert kw["meta"]["error"] == "connection refused"
        assert kw["source"] == "mcp"

    def test_extra_meta_merged(self):
        ev = ToolExecutionEvent(
            ctx=_ctx(source="tools"),
            tool_name="cdp",
            status="ok",
            latency_ms=10.0,
            extra={"server": "browserless", "input_len": 42},
        )
        kw = ev.to_wire_call()
        assert kw["meta"]["server"] == "browserless"
        assert kw["meta"]["input_len"] == 42


# ── ErrorEvent -------------------------------------------------------------


class TestErrorEvent:
    def test_plain_error(self):
        ev = ErrorEvent(
            ctx=_ctx(severity="error"),
            component="guardrails",
            error_message="PII leak detected",
        )
        kw = ev.to_wire_call()
        assert kw["event_type"] == "error"
        assert kw["role"] == "error"
        # source override = component
        assert kw["source"] == "guardrails"
        assert "ERROR guardrails" in kw["content"]

    def test_security_anomaly_shape(self):
        ev = ErrorEvent(
            ctx=_ctx(severity="warn"),
            component="rag_poisoning",
            error_message="similarity outlier",
            risk_level="high",
            confidence=0.91,
            triggers=["magnitude_outlier", "neighbor_inconsistency"],
            action="quarantine",
        )
        kw = ev.to_wire_call()
        assert kw["event_type"] == "security_anomaly"
        assert kw["role"] == "security"
        assert "rag_poisoning risk [high]" in kw["content"]
        assert kw["meta"]["triggers"] == ["magnitude_outlier", "neighbor_inconsistency"]
        assert kw["meta"]["confidence"] == 0.91
        assert kw["meta"]["action"] == "quarantine"

    def test_extraction_attempt_uses_distinct_event_type(self):
        ev = ErrorEvent(
            ctx=_ctx(severity="warn"),
            component="extraction_detector",
            error_message="rapid model switching",
            risk_level="medium",
            confidence=0.7,
            triggers=["model_switch", "high_request_rate"],
        )
        kw = ev.to_wire_call()
        assert kw["event_type"] == "extraction_attempt_detected"
        assert kw["source"] == "extraction_detector"


# ── RoutingEvent -----------------------------------------------------------


class TestRoutingEvent:
    def test_basic_selection(self):
        ev = RoutingEvent(
            ctx=_ctx(source="router"),
            backend="openrouter",
            model="x-ai/grok-4.3",
            reason="fast_tier",
        )
        kw = ev.to_wire_call()
        assert kw["event_type"] == "backend_selection"
        assert kw["role"] == "backend"
        assert kw["source"] == "router"
        assert "openrouter" in kw["content"]
        assert kw["meta"]["reason"] == "fast_tier"

    def test_confidence_optional(self):
        ev = RoutingEvent(
            ctx=_ctx(source="router"),
            backend="openrouter",
            model="m",
            reason="degraded_fallback",
            confidence=0.85,
        )
        kw = ev.to_wire_call()
        assert kw["meta"]["confidence"] == 0.85


# ── PayloadEvent -----------------------------------------------------------


class TestPayloadEvent:
    def test_full_content_carried(self):
        # Per "capture everything", PayloadEvent carries full content,
        # not a truncated summary.
        full_text = "x" * 5000
        ev = PayloadEvent(
            ctx=_ctx(),
            direction="outbound",
            model="gpt-4o",
            backend="openrouter",
            content=full_text,
            tokens=1234,
            cost_usd=0.05,
        )
        kw = ev.to_wire_call()
        assert kw["content"] == full_text   # full string handed through
        assert kw["model"] == "gpt-4o"
        assert kw["direction"] == "outbound"
        assert kw["meta"]["tokens"] == 1234
        assert kw["meta"]["cost_usd"] == 0.05

    def test_summary_merged_into_meta(self):
        ev = PayloadEvent(
            ctx=_ctx(),
            direction="inbound",
            model="m",
            content="hi",
            summary={"finish_reason": "stop", "transforms": ["renamed_max_tokens"]},
        )
        kw = ev.to_wire_call()
        assert kw["meta"]["finish_reason"] == "stop"
        assert kw["meta"]["transforms"] == ["renamed_max_tokens"]


# ── HookExecutionEvent -----------------------------------------------------


class TestHookExecutionEvent:
    def test_zero_hooks_still_emits(self):
        ev = HookExecutionEvent(
            ctx=_ctx(source="hooks"),
            stage="pre_request",
            hook_names=[],
            total_latency_ms=0.3,
        )
        kw = ev.to_wire_call()
        assert kw["event_type"] == "hook_pre_request"
        assert kw["meta"]["hook_count"] == 0
        assert kw["meta"]["error_count"] == 0

    def test_with_errors(self):
        ev = HookExecutionEvent(
            ctx=_ctx(source="hooks"),
            stage="post_response",
            hook_names=["redact_pii"],
            total_latency_ms=12.0,
            hook_errors=[{"hook": "summarize", "error": "timeout"}],
        )
        kw = ev.to_wire_call()
        assert kw["meta"]["hook_count"] == 1
        assert kw["meta"]["error_count"] == 1
        assert "errors=1" in kw["content"]


# ── _wire_kwargs identity threading ---------------------------------------


class TestIdentityFields:
    def test_conv_id_propagated_to_kwargs(self):
        ev = RequestLifecycleEvent(
            ctx=_ctx(conv_id="conv-123"),
            stage="started",
            model="m",
        )
        kw = ev.to_wire_call()
        assert kw.get("conversation_id") == "conv-123"

    def test_run_and_turn_id_propagated(self):
        ev = ToolExecutionEvent(
            ctx=_ctx(source="tools", run_id="run-x", turn_id="turn-y"),
            tool_name="t",
            status="ok",
        )
        kw = ev.to_wire_call()
        assert kw.get("run_id") == "run-x"
        assert kw.get("turn_id") == "turn-y"

    def test_no_id_keys_when_unset(self):
        ev = RequestLifecycleEvent(
            ctx=LogEventContext(source="proxy"),  # no conv/run/turn ids
            stage="started",
            model="m",
        )
        kw = ev.to_wire_call()
        assert "conversation_id" not in kw
        assert "run_id" not in kw
        assert "turn_id" not in kw


# ── emit() dispatch --------------------------------------------------------


class TestEmitDispatch:
    def setup_method(self):
        # Reset the singleton between tests
        set_wire_log(None)

    def teardown_method(self):
        set_wire_log(None)

    def test_emit_noop_when_unbound(self):
        # No exception even though no WireLog is set
        emit(RequestLifecycleEvent(ctx=_ctx(), stage="started", model="m"))
        assert get_wire_log() is None

    def test_emit_dispatches_to_bound_wire_log(self):
        captured = []

        class FakeWireLog:
            def log(self, **kwargs):
                captured.append(kwargs)

        set_wire_log(FakeWireLog())
        emit(RequestLifecycleEvent(
            ctx=_ctx(), stage="started", model="x-ai/grok-4.3", tokens_in=42,
        ))
        assert len(captured) == 1
        assert captured[0]["event_type"] == "request_started"
        assert captured[0]["meta"]["model"] == "x-ai/grok-4.3"

    def test_emit_swallows_sink_exceptions(self):
        class BrokenWireLog:
            def log(self, **kwargs):
                raise RuntimeError("boom")

        set_wire_log(BrokenWireLog())
        # Must not raise
        emit(ToolExecutionEvent(
            ctx=_ctx(source="tools"), tool_name="t", status="ok",
        ))

    def test_set_wire_log_can_be_unset(self):
        class FakeWireLog:
            def log(self, **kwargs):
                pass

        set_wire_log(FakeWireLog())
        assert get_wire_log() is not None
        set_wire_log(None)
        assert get_wire_log() is None
