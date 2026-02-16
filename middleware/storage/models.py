"""
Data models for conversation storage.
These define the shape of data flowing through the middleware.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class Message:
    """A single message in a conversation."""
    id: str = field(default_factory=lambda: uuid4().hex)
    conversation_id: str = ""
    role: str = ""           # "user", "assistant", "system"
    content: str = ""
    model: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    token_count: int = 0     # Approximate, filled in by proxy

    def to_openai_format(self) -> dict:
        """Export in OpenAI messages array format (the portable standard)."""
        return {
            "role": self.role,
            "content": self.content,
            "model": self.model,
            "timestamp": self.timestamp,
        }


@dataclass
class Conversation:
    """A conversation is a list of messages sharing a conversation_id."""
    id: str = field(default_factory=lambda: uuid4().hex)
    messages: list[Message] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_openai_format(self) -> list[dict]:
        return [m.to_openai_format() for m in self.messages]
