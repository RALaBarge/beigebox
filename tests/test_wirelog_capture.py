"""Tests for ``WireLog.write_request`` / ``write_response`` helpers.

These are the new turn-level helpers that take a ``CapturedRequest`` /
``CapturedResponse`` and emit ``model_request_normalized`` /
``model_response_normalized`` events with full canonical metadata. They
delegate fan-out to the existing ``log()`` path, which writes to JSONL
plus any extra sinks (SQLite wire_events, observability egress).
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from beigebox.capture import (
    CaptureContext,
    CapturedRequest,
    CapturedResponse,
)
from beigebox.storage.wire_sink import WireSink
from beigebox.wiretap import WireLog


def _ctx(**overrides) -> CaptureContext:
    base = dict(
        conv_id="c-test",
        turn_id="t-test",
        model="x-ai/grok-4.3",
        backend="openrouter",
        started_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        run_id="r-test",
    )
    base.update(overrides)
    return CaptureContext(**base)


class _CapturingSink(WireSink):
    """Sink that just records every event passed to write()."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)

    def close(self) -> None:
        pass


@pytest.fixture
def wire_log_with_capture():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "wire.jsonl"
        sink = _CapturingSink()
        wl = WireLog(str(log_path), sinks=[sink])
        yield wl, sink, log_path
        wl.close()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().strip().splitlines() if line.strip()]


# --- write_request ----------------------------------------------------------


class TestWriteRequest:
    def test_basic_request_is_emitted(self, wire_log_with_capture):
        wl, sink, log_path = wire_log_with_capture
        req = CapturedRequest(
            ctx=_ctx(),
            target="openrouter",
            transforms=["renamed_max_tokens"],
            errors=[],
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello, world."},
            ],
            has_tools=False,
            stream=False,
        )
        wl.write_request(req)

        # JSONL side
        rows = _read_jsonl(log_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == "model_request_normalized"
        # `source` field is omitted from JSONL when it equals the default "proxy"
        # (existing WireLog optimization); the structured sink still gets it.
        assert row["dir"] == "inbound"
        assert row["role"] == "user"
        # Last user message becomes the displayed content
        assert row["content"] == "Hello, world."

        # SQLite-side fanout
        assert len(sink.events) == 1
        ev = sink.events[0]
        assert ev["event_type"] == "model_request_normalized"
        assert ev["conv_id"] == "c-test"
        assert ev["run_id"] == "r-test"
        meta = ev["meta"]
        assert meta["target"] == "openrouter"
        assert meta["transforms"] == ["renamed_max_tokens"]
        assert meta["has_tools"] is False
        assert meta["stream"] is False
        assert meta["message_count"] == 2
        assert meta["backend"] == "openrouter"
        # Full message list preserved (capture-everything)
        assert meta["request_messages"][0]["role"] == "system"
        assert meta["request_messages"][1]["content"] == "Hello, world."

    def test_streaming_request_meta_reflects_stream_flag(self, wire_log_with_capture):
        wl, sink, _ = wire_log_with_capture
        req = CapturedRequest(
            ctx=_ctx(),
            target="anthropic",
            transforms=["preserved_thinking"],
            errors=[],
            messages=[{"role": "user", "content": "stream me"}],
            has_tools=True,
            stream=True,
        )
        wl.write_request(req)

        meta = sink.events[0]["meta"]
        assert meta["stream"] is True
        assert meta["has_tools"] is True
        assert meta["target"] == "anthropic"

    def test_empty_messages_does_not_crash(self, wire_log_with_capture):
        wl, sink, _ = wire_log_with_capture
        req = CapturedRequest(
            ctx=_ctx(),
            target="openrouter",
            transforms=[],
            errors=[],
            messages=[],
            has_tools=False,
            stream=False,
        )
        wl.write_request(req)

        ev = sink.events[0]
        assert ev["meta"]["message_count"] == 0
        assert ev["meta"]["request_messages"] == []


# --- write_response ---------------------------------------------------------


class TestWriteResponse:
    def test_ok_response_carries_full_meta(self, wire_log_with_capture):
        wl, sink, log_path = wire_log_with_capture
        resp = CapturedResponse(
            ctx=_ctx(latency_ms=420.5, ttft_ms=85.0),
            outcome="ok",
            error_kind=None,
            error_message=None,
            role="assistant",
            content="answer text",
            reasoning="step one\nstep two",
            tool_calls=[{"id": "tc1", "function": {"name": "search"}}],
            finish_reason="tool_calls",
            response_errors=[],
            prompt_tokens=10,
            completion_tokens=20,
            reasoning_tokens=5,
            total_tokens=35,
            cost_usd=0.0042,
        )
        wl.write_response(resp)

        rows = _read_jsonl(log_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == "model_response_normalized"
        assert row["dir"] == "outbound"
        assert row["role"] == "assistant"
        assert row["content"] == "answer text"
        assert row["latency_ms"] == 420.5

        meta = sink.events[0]["meta"]
        assert meta["outcome"] == "ok"
        assert meta["error_kind"] is None
        assert meta["finish_reason"] == "tool_calls"
        assert meta["has_reasoning"] is True
        assert meta["reasoning"] == "step one\nstep two"   # FULL, not just bool
        assert meta["tool_calls_count"] == 1
        assert meta["tool_calls"][0]["id"] == "tc1"        # FULL, not just count
        assert meta["usage"]["prompt_tokens"] == 10
        assert meta["usage"]["reasoning_tokens"] == 5
        assert meta["usage"]["total_tokens"] == 35
        assert meta["cost_usd"] == 0.0042
        assert meta["ttft_ms"] == 85.0

    def test_upstream_error_response_still_emits_row(self, wire_log_with_capture):
        wl, sink, _ = wire_log_with_capture
        resp = CapturedResponse.from_partial(
            ctx=_ctx(latency_ms=1200.0),
            outcome="upstream_error",
            content="",
            error=RuntimeError("connection refused"),
        )
        wl.write_response(resp)

        meta = sink.events[0]["meta"]
        assert meta["outcome"] == "upstream_error"
        assert meta["error_kind"] == "upstream_error"
        assert meta["error_message"] == "connection refused"
        assert meta["finish_reason"] == "error"
        assert meta["reasoning"] is None
        assert meta["tool_calls"] is None

    def test_client_disconnect_with_partial_content(self, wire_log_with_capture):
        wl, sink, _ = wire_log_with_capture
        resp = CapturedResponse.from_partial(
            ctx=_ctx(latency_ms=300.0),
            outcome="client_disconnect",
            content="partial response so far",
        )
        wl.write_response(resp)

        ev = sink.events[0]
        assert ev["meta"]["outcome"] == "client_disconnect"
        assert ev["meta"]["finish_reason"] == "aborted"
        assert ev["content"] == "partial response so far"

    def test_response_with_zero_usage_still_emits_usage_dict(self, wire_log_with_capture):
        wl, sink, _ = wire_log_with_capture
        resp = CapturedResponse(
            ctx=_ctx(),
            outcome="ok",
            error_kind=None,
            error_message=None,
            role="assistant",
            content="x",
            reasoning=None,
            tool_calls=None,
            finish_reason="stop",
            response_errors=[],
        )
        wl.write_response(resp)

        meta = sink.events[0]["meta"]
        assert meta["usage"] == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }
        assert meta["cost_usd"] is None
