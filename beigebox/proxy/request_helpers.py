"""Pure request-side helpers — no proxy state.

These functions inspect or massage the OpenAI-compatible request body before
the orchestrator (``proxy/core.py``) hands it off to a backend. Each helper
takes the body (and any minimal extra context) explicitly and returns a
plain value so they can be exercised in isolation.

Lifted out of ``Proxy`` (was ``proxy.py``) during the G-series refactor:
they had no remaining dependency on ``self`` once the agentic decision
layer was deleted in v3.
"""
from __future__ import annotations

import json
import logging
from uuid import uuid4

from beigebox.aliases import AliasResolver

logger = logging.getLogger(__name__)


def extract_conversation_id(body: dict) -> str:
    """Pull a conversation id out of the request body, generating if missing.

    Open WebUI doesn't always send one. A stable id is required so wiretap
    rows / vector-store messages / sqlite log entries can be correlated to
    a single session. Only generate when messages are present (skip empty
    bodies from health-check-style callers that don't represent real
    sessions).
    """
    conv_id = body.get("conversation_id") or body.get("session_id") or ""
    if not conv_id:
        messages = body.get("messages", [])
        if messages:
            conv_id = uuid4().hex
    return conv_id


def get_model(body: dict, alias_resolver: AliasResolver, default_model: str) -> str:
    """Extract the model from the request, resolve any alias, fall back to default."""
    raw = body.get("model") or default_model
    return alias_resolver.resolve(raw)


def get_latest_user_message(body: dict) -> str:
    """Return the last user message in the body as a plain string.

    OpenAI vision format sends content as a list of typed parts —
    JSON-serialise so downstream consumers always receive a string.
    """
    messages = body.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return content if isinstance(content, str) else json.dumps(content)
    return ""


def is_synthetic(body: dict) -> bool:
    """Check if this request was tagged as synthetic by a hook."""
    return body.get("_beigebox_synthetic", False)


def dedupe_consecutive_messages(body: dict) -> dict:
    """Drop consecutive (role, content) duplicates from ``body['messages']``.

    A buggy or replaying client (e.g. UI double-fire) can send the same
    user message twice in one request body, which then propagates into
    every following turn (the dup sticks in the conversation history).
    This collapses adjacent duplicates so the backend, the wire tap, the
    SQLite store, and the vector index all see a clean sequence.

    Mutates ``body['messages']`` in place and returns body.
    """
    messages = body.get("messages", [])
    if len(messages) < 2:
        return body
    cleaned: list[dict] = []
    prev_key: tuple[str, str] | None = None
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        content_str = content if isinstance(content, str) else json.dumps(content)
        key = (role, content_str)
        if key == prev_key:
            logger.info("dedupe: dropped consecutive duplicate role=%s len=%d",
                        role, len(content_str))
            continue
        cleaned.append(msg)
        prev_key = key
    if len(cleaned) != len(messages):
        body["messages"] = cleaned
    return body
