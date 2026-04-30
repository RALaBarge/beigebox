"""PostgresDB — BaseDB impl over psycopg2."""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

from beigebox.storage.db.base import BaseDB

logger = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2.pool import SimpleConnectionPool
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False


class PostgresDB(BaseDB):
    """PostgreSQL via psycopg2 connection pool.

    Borrows the pool pattern from ``storage/backends/postgres.py`` (the vector
    backend), but is independent — same connection_string can be used for both,
    or they can point at separate databases.

    The transactional model: each top-level call (execute / fetchone / fetchall)
    checks out a connection from the pool, runs, commits, and returns it.
    Inside a ``transaction()`` block, the same connection is held for the
    duration so all statements run atomically.
    """

    def __init__(
        self,
        connection_string: str,
        *,
        pool_size: int = 5,
    ) -> None:
        if not _PSYCOPG2_AVAILABLE:
            raise RuntimeError(
                "psycopg2 not installed. Install with: pip install psycopg2-binary"
            )
        self._dsn = connection_string
        # Per-instance lock for txn-state. Connection mutations are pool-safe.
        self._txn_lock = threading.RLock()
        try:
            self._pool: SimpleConnectionPool = SimpleConnectionPool(1, pool_size, connection_string)
        except psycopg2.Error as e:
            raise psycopg2.Error(
                f"Failed to connect to PostgreSQL at {connection_string}. "
                f"Ensure Postgres is running and the connection string is correct. "
                f"Error: {e}"
            ) from e
        self._last_rowcount = 0
        # Per-thread "are we inside a user-initiated transaction" tracker, so
        # nested transaction() calls reuse the outer connection.
        self._txn_local = threading.local()

    # ─── core ─────────────────────────────────────────────────────────────

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                self._last_rowcount = cur.rowcount
            if not self._in_txn():
                conn.commit()

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                rows = [tuple(r) for r in seq]
                cur.executemany(sql, rows)
                self._last_rowcount = cur.rowcount
            if not self._in_txn():
                conn.commit()

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        with self._connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                row = cur.fetchone()
                self._last_rowcount = cur.rowcount
            if not self._in_txn():
                conn.commit()
            return dict(row) if row is not None else None

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                self._last_rowcount = cur.rowcount
            if not self._in_txn():
                conn.commit()
            return [dict(r) for r in rows]

    @contextmanager
    def transaction(self) -> Iterator["PostgresDB"]:
        if self._in_txn():
            # Reuse outer transaction — no savepoint nesting (BeigeBox doesn't
            # currently need it).
            yield self
            return

        # Pin a connection to this thread for the duration of the transaction.
        conn = self._pool.getconn()
        self._txn_local.conn = conn
        self._txn_local.in_txn = True
        try:
            yield self
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._txn_local.in_txn = False
            self._txn_local.conn = None
            self._pool.putconn(conn)

    def close(self) -> None:
        try:
            self._pool.closeall()
        except Exception:
            pass

    # ─── connection management helper ─────────────────────────────────────

    @contextmanager
    def _connection(self):
        """Yield a connection: the txn-pinned one if we're inside transaction(),
        otherwise a fresh checkout from the pool that's returned on exit."""
        if self._in_txn():
            yield self._txn_local.conn
            return
        conn = self._pool.getconn()
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self._pool.putconn(conn)

    def _in_txn(self) -> bool:
        return bool(getattr(self._txn_local, "in_txn", False))

    # ─── dialect surface ──────────────────────────────────────────────────

    def _placeholder(self) -> str:
        return "%s"

    def _rowcount(self) -> int:
        return self._last_rowcount
