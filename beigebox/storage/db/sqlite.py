"""SqliteDB — BaseDB impl over the stdlib `sqlite3` module."""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from beigebox.storage.db.base import BaseDB

logger = logging.getLogger(__name__)


class SqliteDB(BaseDB):
    """SQLite via stdlib sqlite3.

    Thread-safety: a single ``sqlite3.Connection`` opened with
    ``check_same_thread=False`` and guarded by a per-instance ``RLock``.
    Simpler than a connection pool; SQLite serializes writes anyway.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = 30.0,
        wal: bool = True,
        foreign_keys: bool = True,
    ) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        # check_same_thread=False is safe because every method acquires self._lock
        # before touching the connection.
        self._conn = sqlite3.connect(
            self._path,
            timeout=timeout,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions explicitly
        )
        self._conn.row_factory = sqlite3.Row
        self._last_rowcount = 0
        if wal:
            self._conn.execute("PRAGMA journal_mode=WAL")
        if foreign_keys:
            self._conn.execute("PRAGMA foreign_keys=ON")
        # Track whether we're currently inside a user-initiated transaction.
        self._in_txn = False

    # ─── core ─────────────────────────────────────────────────────────────

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._last_rowcount = cur.rowcount

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            cur = self._conn.executemany(sql, list(seq))
            self._last_rowcount = cur.rowcount

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            row = cur.fetchone()
            return dict(row) if row is not None else None

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    @contextmanager
    def transaction(self) -> Iterator["SqliteDB"]:
        with self._lock:
            if self._in_txn:
                # Reuse outer transaction — no savepoint nesting.
                yield self
                return
            self._conn.execute("BEGIN")
            self._in_txn = True
            try:
                yield self
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            finally:
                self._in_txn = False

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ─── dialect surface ──────────────────────────────────────────────────

    def _placeholder(self) -> str:
        return "?"

    def _rowcount(self) -> int:
        return self._last_rowcount
