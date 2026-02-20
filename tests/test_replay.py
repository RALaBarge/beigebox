"""
Tests for conversation replay.
Run with: pytest tests/test_replay.py
"""

import json
import pytest

from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.models import Message
from beigebox.replay import ConversationReplayer


@pytest.fixture
def store(tmp_path):
    """Create a fresh SQLite store."""
    return SQLiteStore(str(tmp_path / "test.db"))


@pytest.fixture
def wire_path(tmp_path):
    """Create a wiretap log file path."""
    return str(tmp_path / "wire.jsonl")


@pytest.fixture
def replayer(store, wire_path):
    """Create a replayer with test store and wiretap."""
    return ConversationReplayer(store, wiretap_path=wire_path)


def _write_wire(path, entries):
    """Write wiretap entries to JSONL file."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Basic replay
# ---------------------------------------------------------------------------

def test_replay_empty_conversation(replayer):
    """Replay of nonexistent conversation returns error."""
    result = replayer.replay("nonexistent")
    assert result["conversation_id"] == "nonexistent"
    assert "error" in result
    assert result["timeline"] == []


def test_replay_basic(store, replayer):
    """Replay reconstructs messages in order."""
    store.store_message(Message(conversation_id="c1", role="user", content="Hello"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="Hi there!", model="llama3.2"))

    result = replayer.replay("c1")
    assert result["conversation_id"] == "c1"
    assert len(result["timeline"]) == 2
    assert result["timeline"][0]["role"] == "user"
    assert result["timeline"][1]["role"] == "assistant"
    assert result["timeline"][1]["model"] == "llama3.2"


def test_replay_includes_cost(store, replayer):
    """Replay shows cost_usd when available."""
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="answer", model="gpt-4"),
        cost_usd=0.005,
    )

    result = replayer.replay("c1")
    assert result["timeline"][0]["cost_usd"] == 0.005


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def test_replay_stats(store, replayer):
    """Replay computes aggregate stats."""
    store.store_message(Message(conversation_id="c1", role="user", content="q1", model="", token_count=10))
    store.store_message(Message(conversation_id="c1", role="assistant", content="a1", model="llama3.2", token_count=50))
    store.store_message(Message(conversation_id="c1", role="user", content="q2", model="", token_count=15))
    store.store_message(Message(conversation_id="c1", role="assistant", content="a2", model="code", token_count=100))

    result = replayer.replay("c1")
    stats = result["stats"]
    assert stats["message_count"] == 4
    assert stats["total_tokens"] == 175
    assert "llama3.2" in stats["models"]
    assert "code" in stats["models"]


# ---------------------------------------------------------------------------
# Wiretap correlation
# ---------------------------------------------------------------------------

def test_replay_with_routing_decisions(store, wire_path):
    """Replay correlates routing decisions from wiretap."""
    store.store_message(Message(
        id="msg1", conversation_id="c1", role="user", content="hello",
        timestamp="2026-02-20T10:00:00",
    ))
    store.store_message(Message(
        id="msg2", conversation_id="c1", role="assistant", content="hi",
        model="llama3.2", timestamp="2026-02-20T10:00:02",
    ))

    _write_wire(wire_path, [
        {
            "ts": "2026-02-20T10:00:01",
            "dir": "internal",
            "role": "decision",
            "conv": "c1",
            "content": "session cache hit: model=llama3.2",
        },
    ])

    replayer = ConversationReplayer(store, wiretap_path=wire_path)
    result = replayer.replay("c1")

    # Assistant message should have routing info
    assistant = result["timeline"][1]
    assert assistant["routing"]["method"] == "session_cache"
    assert assistant["routing"]["confidence"] == 1.0


def test_replay_with_tools(store, wire_path):
    """Replay detects tool invocations from wiretap."""
    store.store_message(Message(
        id="msg1", conversation_id="c1", role="assistant", content="found it",
        model="llama3.2", timestamp="2026-02-20T10:00:05",
    ))

    _write_wire(wire_path, [
        {
            "ts": "2026-02-20T10:00:03",
            "dir": "internal",
            "role": "tool",
            "conv": "c1",
            "content": "web_search injected (500 chars)",
            "tool": "web_search",
        },
    ])

    replayer = ConversationReplayer(store, wiretap_path=wire_path)
    result = replayer.replay("c1")
    assert "web_search" in result["timeline"][0]["tools"]


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------

def test_replay_text_output(store, replayer):
    """Replay produces readable text."""
    store.store_message(Message(conversation_id="c1", role="user", content="hello"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="world", model="test"))

    result = replayer.replay("c1")
    assert "CONVERSATION REPLAY" in result["text"]
    assert "USER" in result["text"]
    assert "ASSISTANT" in result["text"]
