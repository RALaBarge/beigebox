"""Integration tests for SQLiteStore.store_captured_request/_response.

These hit real SQLite (temp file), populate the new v1.4 columns, and verify
the round trip. They sit between the pure-function capture tests and the
full proxy integration tests.
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from beigebox.capture import (
    CaptureContext,
    CapturedRequest,
    CapturedResponse,
)
from beigebox.storage.sqlite_store import SQLiteStore


def _ctx(**overrides) -> CaptureContext:
    base = dict(
        conv_id="conv-int1",
        turn_id="turn-int1",
        model="x-ai/grok-4.3",
        backend="openrouter",
        started_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 1, 12, 0, 1, tzinfo=timezone.utc),
        latency_ms=1000.0,
        ttft_ms=85.5,
        request_id="req-xyz",
    )
    base.update(overrides)
    return CaptureContext(**base)


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = SQLiteStore(path)
    yield s
    Path(path).unlink(missing_ok=True)


def _row(store: SQLiteStore, msg_id: str) -> dict:
    conn = sqlite3.connect(str(store.db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


class TestStoreCapturedRequest:
    def test_inserts_one_row_per_non_system_message(self, store):
        req = CapturedRequest(
            ctx=_ctx(),
            target="openrouter",
            transforms=["renamed_max_tokens"],
            errors=[],
            messages=[
                {"role": "system", "content": "system prompt"},   # skipped
                {"role": "user", "content": "first user turn"},
                {"role": "assistant", "content": "prior assistant turn"},
                {"role": "user", "content": "current user turn"},
            ],
            has_tools=False,
            stream=False,
        )
        ids = store.store_captured_request(req)

        assert len(ids) == 3   # system skipped, 3 non-system inserted

        row0 = _row(store, ids[0])
        assert row0["role"] == "user"
        assert row0["content"] == "first user turn"
        assert row0["request_transforms_json"] == '["renamed_max_tokens"]'
        assert row0["request_id"] == "req-xyz"
        assert row0["capture_outcome"] == "ok"
        assert row0["error_kind"] is None

    def test_skips_empty_content(self, store):
        req = CapturedRequest(
            ctx=_ctx(),
            target="x", transforms=[], errors=[],
            messages=[
                {"role": "user", "content": ""},      # skipped: empty
                {"role": "user", "content": "real"},
            ],
            has_tools=False, stream=False,
        )
        ids = store.store_captured_request(req)
        assert len(ids) == 1

    def test_normalize_errors_persisted(self, store):
        req = CapturedRequest(
            ctx=_ctx(),
            target="openai_reasoning",
            transforms=[],
            errors=["unknown_param: top_k"],
            messages=[{"role": "user", "content": "hi"}],
            has_tools=False, stream=False,
        )
        ids = store.store_captured_request(req)
        row = _row(store, ids[0])
        assert row["normalize_errors_json"] == '["unknown_param: top_k"]'

    def test_creates_conversation_row(self, store):
        req = CapturedRequest(
            ctx=_ctx(conv_id="conv-new"),
            target="x", transforms=[], errors=[],
            messages=[{"role": "user", "content": "hi"}],
            has_tools=False, stream=False,
        )
        store.store_captured_request(req)

        conn = sqlite3.connect(str(store.db_path))
        try:
            row = conn.execute(
                "SELECT id FROM conversations WHERE id = ?", ("conv-new",)
            ).fetchone()
            assert row is not None
        finally:
            conn.close()


class TestStoreCapturedResponse:
    def test_full_response_persists_all_fields(self, store):
        resp = CapturedResponse(
            ctx=_ctx(),
            outcome="ok",
            error_kind=None,
            error_message=None,
            role="assistant",
            content="answer text",
            reasoning="step 1\nstep 2",
            tool_calls=[{"id": "tc1", "function": {"name": "search"}}],
            finish_reason="tool_calls",
            response_errors=[],
            prompt_tokens=10,
            completion_tokens=20,
            reasoning_tokens=5,
            total_tokens=35,
            cost_usd=0.0042,
        )
        msg_id = store.store_captured_response(resp)
        row = _row(store, msg_id)

        assert row["content"] == "answer text"
        assert row["reasoning_text"] == "step 1\nstep 2"
        assert row["tool_calls_json"] == '[{"id": "tc1", "function": {"name": "search"}}]'
        assert row["finish_reason"] == "tool_calls"
        assert row["prompt_tokens"] == 10
        assert row["completion_tokens"] == 20
        assert row["reasoning_tokens"] == 5
        assert row["cost_usd"] == 0.0042
        assert row["latency_ms"] == 1000.0
        assert row["ttft_ms"] == 85.5
        assert row["request_id"] == "req-xyz"
        assert row["capture_outcome"] == "ok"
        assert row["error_kind"] is None

    def test_failure_response_persists_with_outcome_and_partial_content(self, store):
        resp = CapturedResponse.from_partial(
            ctx=_ctx(),
            outcome="stream_aborted",
            content="partial assembled text",
            error=RuntimeError("upstream 502"),
        )
        msg_id = store.store_captured_response(resp)
        row = _row(store, msg_id)

        assert row["content"] == "partial assembled text"
        assert row["capture_outcome"] == "stream_aborted"
        assert row["error_kind"] == "stream_aborted"
        assert row["error_message"] == "upstream 502"
        assert row["finish_reason"] == "error"
        assert row["reasoning_text"] is None
        assert row["tool_calls_json"] is None

    def test_client_disconnect_outcome(self, store):
        resp = CapturedResponse.from_partial(
            ctx=_ctx(),
            outcome="client_disconnect",
            content="some bytes that made it",
        )
        msg_id = store.store_captured_response(resp)
        row = _row(store, msg_id)

        assert row["capture_outcome"] == "client_disconnect"
        assert row["finish_reason"] == "aborted"
        assert row["error_message"] is None

    def test_response_with_no_tool_calls_stores_null_json(self, store):
        resp = CapturedResponse(
            ctx=_ctx(), outcome="ok", error_kind=None, error_message=None,
            role="assistant", content="x", reasoning=None, tool_calls=None,
            finish_reason="stop", response_errors=[],
        )
        msg_id = store.store_captured_response(resp)
        row = _row(store, msg_id)

        assert row["tool_calls_json"] is None
        assert row["reasoning_text"] is None
        assert row["normalize_errors_json"] is None

    def test_long_reasoning_text_stored_raw_no_truncation(self, store):
        # User decision (locked in plan): reasoning text is stored raw, no cap.
        long_reasoning = "step\n" * 5000   # ~25KB of reasoning
        resp = CapturedResponse(
            ctx=_ctx(), outcome="ok", error_kind=None, error_message=None,
            role="assistant", content="final answer", reasoning=long_reasoning,
            tool_calls=None, finish_reason="stop", response_errors=[],
        )
        msg_id = store.store_captured_response(resp)
        row = _row(store, msg_id)

        assert row["reasoning_text"] == long_reasoning
        assert len(row["reasoning_text"]) > 20000
