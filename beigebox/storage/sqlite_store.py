"""SQLiteStore — deprecated; kept only as a thin compatibility shim.

The real implementation lives in:
- ``beigebox/storage/db/`` (BaseDB shim)
- ``beigebox/storage/repos/conversations.py`` (ConversationRepo)
- ``beigebox/storage/repos/{api_keys,quarantine,users,wire_events}.py``

The legacy ``SQLiteStore(path, integrity_config=...)`` call site returned a
god-object that owned every table. Production callers (proxy.py, main.py,
cli.py, replay.py, costs.py, memory_validator_tool.py) all migrated to
the per-entity repos in batch B (commit 9ed5b55). This shim exists so
tests written against the old API keep working until they're rewritten.

What you get from ``SQLiteStore(path)`` today is a ``ConversationRepo``
with ``create_tables()`` already called. Other repos (api_keys, quarantine,
users, wire_events) need to be constructed separately if a test needs them.

Plan to delete this file once test_memory_integrity, test_memory_validator,
test_storage, test_v08, test_messages_schema_migration, and
test_capture_sqlite_integration migrate to ``make_conversation_repo``.
"""
from __future__ import annotations

from typing import Any

from beigebox.storage.db import make_db
from beigebox.storage.repos import make_conversation_repo


def SQLiteStore(db_path: str, integrity_config: dict | None = None) -> Any:
    """Compatibility shim: build a ConversationRepo backed by a fresh BaseDB.

    Returns a ``ConversationRepo`` instance with ``create_tables()`` already
    called. The instance has the same methods the legacy SQLiteStore class
    exposed (ensure_conversation, store_message, store_captured_request,
    store_captured_response, get_conversation, get_recent_conversations,
    get_model_performance, fork_conversation, export_*, get_stats), plus
    ``_db`` for tests that previously reached into ``store._connect()``.

    Tests that need a different table set (api_keys, quarantine, users,
    wire_events) should use the per-entity ``make_*_repo(db)`` factories
    directly instead.
    """
    db = make_db("sqlite", path=str(db_path))
    repo = make_conversation_repo(db, integrity_config=integrity_config)
    repo.create_tables()
    # Keep the underlying BaseDB on the instance as ``_connect``-flavoured
    # access path for tests that haven't migrated yet. ``store._db`` is the
    # public escape hatch; ``store._connect`` is preserved as a context
    # manager that yields a sqlite3 connection so the small handful of tests
    # that do raw cursor work keep working.
    import sqlite3
    from contextlib import contextmanager

    @contextmanager
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    repo._connect = _connect  # type: ignore[attr-defined]
    repo.db_path = db_path  # type: ignore[attr-defined]
    return repo
