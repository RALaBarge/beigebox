"""
Tests for SQLite storage.
Uses a temp database for each test.
"""

import pytest
import tempfile
import os
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.models import Message


@pytest.fixture
def store(tmp_path):
    """Create a fresh SQLite store for each test."""
    db_path = str(tmp_path / "test.db")
    return SQLiteStore(db_path)


def test_store_and_retrieve(store):
    """Store a message and get it back."""
    msg = Message(conversation_id="conv1", role="user", content="hello world", model="qwen3:32b")
    store.store_message(msg)

    messages = store.get_conversation("conv1")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello world"


def test_multiple_messages(store):
    """Store multiple messages in same conversation."""
    store.store_message(Message(conversation_id="conv1", role="user", content="hi"))
    store.store_message(Message(conversation_id="conv1", role="assistant", content="hello!"))
    store.store_message(Message(conversation_id="conv1", role="user", content="how are you?"))

    messages = store.get_conversation("conv1")
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_separate_conversations(store):
    """Messages in different conversations stay separate."""
    store.store_message(Message(conversation_id="conv1", role="user", content="msg1"))
    store.store_message(Message(conversation_id="conv2", role="user", content="msg2"))

    assert len(store.get_conversation("conv1")) == 1
    assert len(store.get_conversation("conv2")) == 1


def test_stats(store):
    """Stats reflect stored data."""
    store.store_message(Message(conversation_id="c1", role="user", content="a"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="b"))
    store.store_message(Message(conversation_id="c2", role="user", content="c"))

    stats = store.get_stats()
    assert stats["conversations"] == 2
    assert stats["messages"] == 3
    assert stats["user_messages"] == 2
    assert stats["assistant_messages"] == 1


def test_export_json(store):
    """Export produces OpenAI-compatible format."""
    store.store_message(Message(conversation_id="c1", role="user", content="hello", model="test"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="hi", model="test"))

    export = store.export_all_json()
    assert len(export) == 1
    assert export[0]["conversation_id"] == "c1"
    assert len(export[0]["messages"]) == 2
    assert export[0]["messages"][0]["role"] == "user"
