"""Tests for ConversationRepo (BaseDB-backed conversation+message storage).

Covers DDL, the legacy ``store_message`` writer, the v1.4 capture writers,
read methods (get_conversation, get_recent_conversations,
get_model_performance, exports, get_stats), forking, and HMAC integrity
round-trip.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from beigebox.capture import CaptureContext, CapturedRequest, CapturedResponse
from beigebox.storage.db import make_db
from beigebox.storage.models import Message
from beigebox.storage.repos import make_conversation_repo


@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db = make_db("sqlite", path=path)
    r = make_conversation_repo(db)
    r.create_tables()
    yield r
    db.close()
    Path(path).unlink(missing_ok=True)


def _ctx(**overrides) -> CaptureContext:
    base = dict(
        conv_id="c1",
        turn_id="t1",
        model="gpt-4o-mini",
        backend="openrouter",
        started_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 1, 12, 0, 1, tzinfo=timezone.utc),
        latency_ms=1000.0,
    )
    base.update(overrides)
    return CaptureContext(**base)


class TestSchema:
    def test_create_tables_idempotent(self, repo):
        repo.create_tables()  # second call must not error
        repo.create_tables()
        repo.ensure_conversation("c-x", "2026-01-01T00:00:00Z")


class TestEnsureConversation:
    def test_creates_row(self, repo):
        repo.ensure_conversation("c1", "2026-05-01T00:00:00Z")
        rows = repo._db.fetchall("SELECT id FROM conversations WHERE id = 'c1'")
        assert len(rows) == 1

    def test_idempotent(self, repo):
        repo.ensure_conversation("c1", "2026-05-01T00:00:00Z")
        repo.ensure_conversation("c1", "2026-05-01T00:00:00Z")
        rows = repo._db.fetchall("SELECT id FROM conversations WHERE id = 'c1'")
        assert len(rows) == 1


class TestStoreMessage:
    def test_basic_store(self, repo):
        msg = Message(
            id="m1", conversation_id="c1", role="user", content="hi",
            model="gpt-4o", token_count=2,
            timestamp="2026-05-01T12:00:00Z",
        )
        repo.store_message(msg)
        rows = repo._db.fetchall("SELECT * FROM messages WHERE id = 'm1'")
        assert rows[0]["content"] == "hi"

    def test_with_cost_and_latency(self, repo):
        msg = Message(
            id="m2", conversation_id="c1", role="assistant",
            content="answer", token_count=10,
            timestamp="2026-05-01T12:00:01Z",
        )
        repo.store_message(msg, cost_usd=0.0042, latency_ms=300.0, ttft_ms=50.0)
        row = repo._db.fetchone("SELECT * FROM messages WHERE id = 'm2'")
        assert row["cost_usd"] == 0.0042
        assert row["latency_ms"] == 300.0
        assert row["ttft_ms"] == 50.0


class TestStoreCapturedRequest:
    def test_one_row_per_non_system_message(self, repo):
        req = CapturedRequest(
            ctx=_ctx(),
            target="openrouter",
            transforms=["renamed_max_tokens"],
            errors=[],
            messages=[
                {"role": "system", "content": "skip"},
                {"role": "user", "content": "first"},
                {"role": "user", "content": "second"},
            ],
            has_tools=False, stream=False,
        )
        ids = repo.store_captured_request(req)
        assert len(ids) == 2

        rows = repo._db.fetchall(
            "SELECT * FROM messages WHERE id IN (?, ?) ORDER BY id",
            tuple(sorted(ids)),
        )
        assert all(r["request_transforms_json"] == '["renamed_max_tokens"]' for r in rows)
        assert all(r["capture_outcome"] == "ok" for r in rows)


class TestStoreCapturedResponse:
    def test_ok_response(self, repo):
        resp = CapturedResponse(
            ctx=_ctx(),
            outcome="ok",
            error_kind=None, error_message=None,
            role="assistant", content="answer",
            reasoning="thinking", tool_calls=None,
            finish_reason="stop", response_errors=[],
            prompt_tokens=10, completion_tokens=20,
            reasoning_tokens=5, total_tokens=35, cost_usd=0.001,
        )
        msg_id = repo.store_captured_response(resp)
        row = repo._db.fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
        assert row["reasoning_text"] == "thinking"
        assert row["finish_reason"] == "stop"
        assert row["prompt_tokens"] == 10
        assert row["capture_outcome"] == "ok"

    def test_failure_response_persists_error(self, repo):
        resp = CapturedResponse.from_partial(
            ctx=_ctx(),
            outcome="upstream_error",
            content="",
            error=RuntimeError("502"),
        )
        msg_id = repo.store_captured_response(resp)
        row = repo._db.fetchone("SELECT * FROM messages WHERE id = ?", (msg_id,))
        assert row["capture_outcome"] == "upstream_error"
        assert "502" in (row["error_message"] or "")
        assert row["finish_reason"] == "error"


class TestGetConversation:
    def test_empty(self, repo):
        msgs, status = repo.get_conversation("nonexistent")
        assert msgs == []
        assert status["valid"] is True

    def test_returns_in_timestamp_order(self, repo):
        for i, ts in enumerate([
            "2026-05-01T12:00:03Z", "2026-05-01T12:00:01Z", "2026-05-01T12:00:02Z",
        ]):
            repo.store_message(Message(
                id=f"m{i}", conversation_id="c1", role="user",
                content=f"msg{i}", timestamp=ts,
            ))
        msgs, _ = repo.get_conversation("c1")
        timestamps = [m["timestamp"] for m in msgs]
        assert timestamps == sorted(timestamps)


class TestGetRecentConversations:
    def test_returns_with_last_message_and_count(self, repo):
        repo.ensure_conversation("c-old", "2026-04-01T00:00:00Z")
        repo.ensure_conversation("c-new", "2026-05-01T00:00:00Z")
        repo.store_message(Message(
            id="m-new", conversation_id="c-new", role="user",
            content="latest", timestamp="2026-05-01T00:00:01Z",
        ))
        rows = repo.get_recent_conversations(limit=10)
        assert len(rows) >= 2
        # newest first
        assert rows[0]["id"] == "c-new"
        assert rows[0]["last_message"] == "latest"
        assert rows[0]["message_count"] == 1


class TestForkConversation:
    def test_clone_full_conversation(self, repo):
        for i in range(3):
            repo.store_message(Message(
                id=f"src{i}", conversation_id="c-src", role="user" if i % 2 == 0 else "assistant",
                content=f"turn {i}", timestamp=f"2026-05-01T12:00:0{i}Z",
            ))
        copied = repo.fork_conversation("c-src", "c-dst")
        assert copied == 3
        dst_msgs, _ = repo.get_conversation("c-dst")
        assert [m["content"] for m in dst_msgs] == ["turn 0", "turn 1", "turn 2"]

    def test_branch_at_index(self, repo):
        for i in range(4):
            repo.store_message(Message(
                id=f"src{i}", conversation_id="c-src", role="user",
                content=f"turn {i}", timestamp=f"2026-05-01T12:00:0{i}Z",
            ))
        copied = repo.fork_conversation("c-src", "c-fork", branch_at=1)
        assert copied == 2
        dst_msgs, _ = repo.get_conversation("c-fork")
        assert [m["content"] for m in dst_msgs] == ["turn 0", "turn 1"]


class TestExports:
    def _seed(self, repo):
        for ts, role, content in [
            ("2026-05-01T12:00:00Z", "user", "Q1"),
            ("2026-05-01T12:00:01Z", "assistant", "A1"),
            ("2026-05-01T12:00:02Z", "user", "Q2"),
            ("2026-05-01T12:00:03Z", "assistant", "A2"),
        ]:
            repo.store_message(Message(
                conversation_id="c1", role=role, content=content,
                model="gpt-4o", timestamp=ts,
            ))

    def test_export_all_json(self, repo):
        self._seed(repo)
        out = repo.export_all_json()
        assert len(out) == 1
        assert len(out[0]["messages"]) == 4

    def test_export_jsonl_filters_to_user_assistant_pairs(self, repo):
        self._seed(repo)
        out = repo.export_jsonl()
        assert len(out) == 1
        roles = {m["role"] for m in out[0]["messages"]}
        assert roles == {"user", "assistant"}

    def test_export_alpaca_pairs_user_with_following_assistant(self, repo):
        self._seed(repo)
        out = repo.export_alpaca()
        assert len(out) == 2
        assert out[0]["instruction"] == "Q1"
        assert out[0]["output"] == "A1"

    def test_export_sharegpt_uses_human_gpt_keys(self, repo):
        self._seed(repo)
        out = repo.export_sharegpt()
        assert len(out) == 1
        froms = {m["from"] for m in out[0]["conversations"]}
        assert froms == {"human", "gpt"}


class TestGetStats:
    def test_empty(self, repo):
        s = repo.get_stats()
        assert s["conversations"] == 0
        assert s["messages"] == 0

    def test_populated(self, repo):
        repo.store_message(Message(
            conversation_id="c1", role="user", content="hi",
            model="gpt-4o", token_count=5,
            timestamp="2026-05-01T12:00:00Z",
        ))
        repo.store_message(Message(
            conversation_id="c1", role="assistant", content="hello",
            model="gpt-4o", token_count=10,
            timestamp="2026-05-01T12:00:01Z",
        ), cost_usd=0.0001)
        s = repo.get_stats()
        assert s["conversations"] == 1
        assert s["messages"] == 2
        assert s["user_messages"] == 1
        assert s["assistant_messages"] == 1
        assert s["tokens"]["total"] == 15
        assert s["models"]["gpt-4o"]["cost_usd"] == 0.0001


class TestGetModelPerformance:
    def test_empty_returns_empty_by_model(self, repo):
        out = repo.get_model_performance()
        assert out["by_model"] == {}

    def test_aggregates_by_model(self, repo):
        for i in range(3):
            repo.store_message(Message(
                conversation_id="c1", role="assistant",
                content=f"a{i}", model="gpt-4o", token_count=10,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ), latency_ms=100.0 + i * 50, ttft_ms=20.0)
        out = repo.get_model_performance(days=1)
        assert "gpt-4o" in out["by_model"]
        m = out["by_model"]["gpt-4o"]
        assert m["requests"] == 3
        assert m["p50_latency_ms"] > 0


class TestIntegrityRoundTrip:
    """Verify HMAC signing + verification round-trips for a real key.

    Disabled-mode is the default — these tests configure an integrity
    validator with an env-source dev key and exercise the sign/verify
    handshake.
    """

    @pytest.fixture
    def signed_repo(self, monkeypatch):
        # Use dev_mode=True so KeyManager generates an ephemeral key
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        db = make_db("sqlite", path=path)
        # The integrity config enables HMAC + uses a dev-mode key
        r = make_conversation_repo(db, integrity_config={
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": True,
        })
        r.create_tables()
        yield r
        db.close()
        Path(path).unlink(missing_ok=True)

    def test_signed_message_verifies_clean(self, signed_repo):
        if signed_repo.integrity_validator is None:
            pytest.skip("dev key not available in this env")

        msg = Message(
            id="m-sign", conversation_id="c-sign", role="user",
            content="signed content", model="gpt-4o", token_count=3,
            timestamp="2026-05-01T12:00:00Z",
        )
        signed_repo.store_message(msg, user_id="u1")

        msgs, status = signed_repo.get_conversation("c-sign", user_id="u1")
        assert status["valid"] is True
        assert msgs[0]["message_hmac"] is not None
        assert status["tampered_messages"] == []
        assert status["unsigned_messages"] == []

    def test_tampered_content_detected(self, signed_repo):
        if signed_repo.integrity_validator is None:
            pytest.skip("dev key not available in this env")

        msg = Message(
            id="m-tamper", conversation_id="c-tamper", role="user",
            content="original", model="gpt-4o", token_count=2,
            timestamp="2026-05-01T12:00:00Z",
        )
        signed_repo.store_message(msg, user_id="u1")

        # Tamper directly via the DB shim
        signed_repo._db.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            ("tampered!", "m-tamper"),
        )

        _, status = signed_repo.get_conversation("c-tamper", user_id="u1")
        assert status["valid"] is False
        assert "m-tamper" in status["tampered_messages"]
