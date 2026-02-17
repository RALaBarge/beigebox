"""
Tests for proxy layer and data models.
Run with: pytest tests/
"""

import pytest
from beigebox.storage.models import Message


def test_message_creation():
    """Message gets created with defaults."""
    msg = Message(conversation_id="abc", role="user", content="hello")
    assert msg.conversation_id == "abc"
    assert msg.role == "user"
    assert msg.content == "hello"
    assert msg.id
    assert msg.timestamp


def test_message_openai_format():
    """Message exports to OpenAI format correctly."""
    msg = Message(
        conversation_id="abc",
        role="assistant",
        content="world",
        model="qwen3:32b",
    )
    fmt = msg.to_openai_format()
    assert fmt["role"] == "assistant"
    assert fmt["content"] == "world"
    assert fmt["model"] == "qwen3:32b"
    assert "timestamp" in fmt


def test_message_token_count():
    """Message stores token count."""
    msg = Message(conversation_id="abc", role="user", content="hello", token_count=42)
    assert msg.token_count == 42
