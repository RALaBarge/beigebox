"""Tests for the v1.4 messages-schema migration.

Verifies that:
- A legacy DB (one without the v1.4 columns) gets the new columns added on
  next ``SQLiteStore`` init, with NULL defaults for existing rows.
- Re-running the migration is a no-op (no errors on duplicate columns).
- A fresh DB created from CREATE_TABLES has the v1.4 columns from the start.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from beigebox.storage.db import make_db
from beigebox.storage.repos import make_conversation_repo


def _migrate_legacy_db(path: Path) -> None:
    """Apply ConversationRepo's idempotent CREATE + ALTER chain to a path.

    Replaces ``SQLiteStore(path)``'s old behaviour: open a fresh BaseDB
    and call ``create_tables()``, which runs the same DDL + migration
    list that the legacy SQLiteStore.__init__ used.
    """
    db = make_db("sqlite", path=str(path))
    try:
        repo = make_conversation_repo(db)
        repo.create_tables()
    finally:
        db.close()


V14_COLUMNS = [
    "reasoning_text",
    "tool_calls_json",
    "finish_reason",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "request_transforms_json",
    "normalize_errors_json",
    "request_id",
    "capture_outcome",
    "error_kind",
    "error_message",
]


def _columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("PRAGMA table_info(messages)")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        p = Path(f.name)
    yield p
    p.unlink(missing_ok=True)


def _create_legacy_messages_table(db_path: Path) -> None:
    """Create a pre-v1.4 messages table (only columns up through v1.3)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                user_id TEXT DEFAULT NULL,
                integrity_checked_at TEXT DEFAULT NULL
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                token_count INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT NULL,
                latency_ms REAL DEFAULT NULL,
                ttft_ms REAL DEFAULT NULL,
                custom_field_1 TEXT DEFAULT NULL,
                custom_field_2 TEXT DEFAULT NULL,
                message_hmac TEXT DEFAULT NULL,
                integrity_version INTEGER DEFAULT 1,
                tamper_detected BOOLEAN DEFAULT 0
            );
            INSERT INTO conversations (id, created_at) VALUES ('c-legacy', '2026-01-01T00:00:00Z');
            INSERT INTO messages (id, conversation_id, role, content, timestamp)
                VALUES ('m-legacy', 'c-legacy', 'user', 'hello from before v1.4', '2026-01-01T00:00:00Z');
        """)
        conn.commit()
    finally:
        conn.close()


def test_legacy_db_gets_v14_columns_added(db_path):
    _create_legacy_messages_table(db_path)
    cols_before = _columns(db_path)
    for new_col in V14_COLUMNS:
        assert new_col not in cols_before, f"setup error: {new_col} already present"

    _migrate_legacy_db(db_path)

    cols_after = _columns(db_path)
    for new_col in V14_COLUMNS:
        assert new_col in cols_after, f"v1.4 migration did not add {new_col}"


def test_legacy_row_survives_with_null_v14_fields(db_path):
    _create_legacy_messages_table(db_path)
    _migrate_legacy_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT id, content, reasoning_text, tool_calls_json, "
            "prompt_tokens, capture_outcome FROM messages WHERE id = 'm-legacy'"
        )
        row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "m-legacy"
    assert row[1] == "hello from before v1.4"
    assert row[2] is None  # reasoning_text
    assert row[3] is None  # tool_calls_json
    assert row[4] is None  # prompt_tokens
    assert row[5] is None  # capture_outcome


def test_migration_is_idempotent(db_path):
    _create_legacy_messages_table(db_path)
    _migrate_legacy_db(db_path)
    cols_first = _columns(db_path)

    _migrate_legacy_db(db_path)  # second init must not error
    _migrate_legacy_db(db_path)  # third for good measure
    cols_third = _columns(db_path)

    assert cols_first == cols_third


def test_fresh_db_has_v14_columns_from_create(db_path):
    _migrate_legacy_db(db_path)

    cols = _columns(db_path)
    for new_col in V14_COLUMNS:
        assert new_col in cols, f"fresh DB missing {new_col} from CREATE TABLE"


def test_v14_columns_accept_inserts(db_path):
    _migrate_legacy_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO conversations (id, created_at) VALUES (?, ?)",
            ("c-v14", "2026-05-01T12:00:00Z"),
        )
        conn.execute(
            """INSERT INTO messages (
                id, conversation_id, role, content, timestamp,
                reasoning_text, tool_calls_json, finish_reason,
                prompt_tokens, completion_tokens, reasoning_tokens,
                request_transforms_json, normalize_errors_json, request_id,
                capture_outcome, error_kind, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "m-v14", "c-v14", "assistant", "answer", "2026-05-01T12:00:01Z",
                "step 1\nstep 2", '[{"id":"tc1"}]', "tool_calls",
                10, 20, 5,
                '["renamed_max_tokens"]', "[]", "req-abc",
                "ok", None, None,
            ),
        )
        conn.commit()

        row = conn.execute(
            "SELECT reasoning_text, prompt_tokens, capture_outcome FROM messages WHERE id = 'm-v14'"
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == "step 1\nstep 2"
    assert row[1] == 10
    assert row[2] == "ok"
