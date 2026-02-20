"""
Tests for cost tracking.
Run with: pytest tests/test_costs.py
"""

import pytest
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.models import Message
from beigebox.costs import CostTracker


@pytest.fixture
def store(tmp_path):
    """Create a fresh SQLite store."""
    return SQLiteStore(str(tmp_path / "test.db"))


@pytest.fixture
def tracker(store):
    """Create a CostTracker backed by the test store."""
    return CostTracker(store)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_schema_has_cost_column(store):
    """Messages table includes cost_usd column."""
    with store._connect() as conn:
        info = conn.execute("PRAGMA table_info(messages)").fetchall()
        columns = [row["name"] for row in info]
    assert "cost_usd" in columns
    assert "custom_field_1" in columns
    assert "custom_field_2" in columns


def test_migration_idempotent(tmp_path):
    """Running init twice doesn't crash (migration is safe to re-run)."""
    db = str(tmp_path / "test.db")
    s1 = SQLiteStore(db)
    s2 = SQLiteStore(db)  # Should not raise
    assert s2 is not None


# ---------------------------------------------------------------------------
# Storing cost
# ---------------------------------------------------------------------------

def test_store_message_with_cost(store):
    """Messages can be stored with cost_usd."""
    msg = Message(conversation_id="c1", role="assistant", content="hi", model="gpt-4")
    store.store_message(msg, cost_usd=0.0015)

    messages = store.get_conversation("c1")
    assert len(messages) == 1
    assert messages[0]["cost_usd"] == 0.0015


def test_store_message_without_cost(store):
    """Messages without cost default to NULL."""
    msg = Message(conversation_id="c1", role="assistant", content="hi", model="llama3.2")
    store.store_message(msg)

    messages = store.get_conversation("c1")
    assert messages[0]["cost_usd"] is None


def test_stats_include_cost(store):
    """get_stats includes total cost and per-model cost."""
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="a", model="gpt-4", token_count=100),
        cost_usd=0.01,
    )
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="b", model="gpt-4", token_count=50),
        cost_usd=0.005,
    )
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="c", model="llama3.2", token_count=200),
    )

    stats = store.get_stats()
    assert stats["cost_usd"] == pytest.approx(0.015)
    assert stats["models"]["gpt-4"]["cost_usd"] == pytest.approx(0.015)
    assert stats["models"]["llama3.2"]["cost_usd"] == 0


# ---------------------------------------------------------------------------
# CostTracker queries
# ---------------------------------------------------------------------------

def test_tracker_empty(tracker):
    """CostTracker handles empty database."""
    stats = tracker.get_stats(days=30)
    assert stats["total"] == 0
    assert stats["by_model"] == {}
    assert stats["by_day"] == {}


def test_tracker_total(store, tracker):
    """CostTracker computes total cost."""
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="a", model="gpt-4"),
        cost_usd=0.01,
    )
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="b", model="gpt-4"),
        cost_usd=0.02,
    )
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="c", model="local"),
    )

    assert tracker.get_total() == pytest.approx(0.03)


def test_tracker_by_model(store, tracker):
    """CostTracker breaks down cost by model."""
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="a", model="gpt-4"),
        cost_usd=0.01,
    )
    store.store_message(
        Message(conversation_id="c1", role="assistant", content="b", model="claude-3"),
        cost_usd=0.005,
    )

    stats = tracker.get_stats(days=30)
    assert "gpt-4" in stats["by_model"]
    assert stats["by_model"]["gpt-4"]["cost"] == pytest.approx(0.01)
    assert "claude-3" in stats["by_model"]


def test_tracker_by_conversation(store, tracker):
    """CostTracker shows top conversations by cost."""
    store.store_message(
        Message(conversation_id="expensive", role="assistant", content="a", model="gpt-4"),
        cost_usd=0.50,
    )
    store.store_message(
        Message(conversation_id="cheap", role="assistant", content="b", model="gpt-4"),
        cost_usd=0.001,
    )

    stats = tracker.get_stats(days=30)
    convs = stats["by_conversation"]
    assert len(convs) >= 2
    # First should be most expensive
    assert convs[0]["conversation_id"] == "expensive"
    assert convs[0]["cost"] == pytest.approx(0.50)
