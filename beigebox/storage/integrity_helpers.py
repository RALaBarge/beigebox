"""Shared helpers for memory-integrity HMAC signing.

The set of fields that get signed is the contract between writers
(``ConversationRepo.store_message`` / ``store_captured_*``) and
readers/patchers (``ConversationRepo.get_conversation``,
``security/memory_validator_tool.py``). Centralizing it here prevents
silent drift — any change to the signed-field set must be made in one
place, and re-applied to every previously-signed row.

Do not add v1.4 capture columns (reasoning_text, tool_calls_json,
finish_reason, etc.) to ``SIGNABLE_FIELDS`` without a coordinated
re-sign of every row already on disk. Adding fields invalidates every
existing signature.
"""
from __future__ import annotations


SIGNABLE_FIELDS: frozenset[str] = frozenset({
    "id",
    "conversation_id",
    "role",
    "content",
    "model",
    "timestamp",
    "token_count",
})


def extract_signable_fields(msg: dict) -> dict:
    """Return only the signable subset of a message dict.

    Used both at write time (to produce the HMAC payload) and at read
    time (to verify it). Both sides must agree on the field set, so
    ``SIGNABLE_FIELDS`` is the single source of truth.
    """
    return {k: v for k, v in msg.items() if k in SIGNABLE_FIELDS}
