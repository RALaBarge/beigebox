"""
BaseDB — the generic SQL shim.

Every relational backend (sqlite, postgres, in-memory) implements this
contract. Entity-specific repositories (ConversationRepo, KeyRepo, etc.)
take a BaseDB instance and don't care which dialect is underneath.

The dialect surface is intentionally tiny: only the parameter placeholder
(``?`` vs ``%s``) and the rowcount accessor. Modern SQLite (≥3.35) and
PostgreSQL both speak the same ``ON CONFLICT ... DO UPDATE`` and
``RETURNING`` syntax, so everything else lives in default impls on this
class.

Mirrors the pattern in beigebox/storage/backends/ (vector storage) — see
``beigebox/orientation.md`` for the architectural stance.
"""
from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

logger = logging.getLogger(__name__)


class BaseDB(ABC):
    """Generic relational DB shim — connection, queries, transactions, migrations."""

    # ─── core query primitives (abstract) ─────────────────────────────────

    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Run a statement that doesn't return rows (INSERT/UPDATE/DELETE/DDL)."""

    @abstractmethod
    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        """Run the same statement against many parameter rows in one transaction."""

    @abstractmethod
    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        """Run a SELECT and return the first row as a dict, or None."""

    @abstractmethod
    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        """Run a SELECT and return all rows as a list of dicts."""

    @contextmanager
    @abstractmethod
    def transaction(self) -> Iterator["BaseDB"]:
        """Open a transaction. On exception → rollback. On clean exit → commit.

        Yields self so callers can do ``with db.transaction() as tx: tx.execute(...)``.
        Nested calls reuse the outer transaction (no savepoints; not currently needed).
        """

    @abstractmethod
    def close(self) -> None:
        """Release pool / file handles. Idempotent."""

    # ─── dialect surface (abstract — minimal) ─────────────────────────────

    @abstractmethod
    def _placeholder(self) -> str:
        """``?`` for sqlite, ``%s`` for psycopg2."""

    @abstractmethod
    def _rowcount(self) -> int:
        """Affected-row count from the most recent execute() / executemany()."""

    # ─── high-level CRUD helpers (default impls — covers ~95% of usage) ──

    def insert(self, table: str, row: dict) -> int | None:
        """INSERT a row dict; return the auto-increment id, or None.

        Uses standard ``RETURNING id`` (SQLite ≥3.35, all PostgreSQL).
        For tables with caller-generated keys, just use ``execute()`` directly.
        """
        if not row:
            raise ValueError("insert() requires a non-empty row dict")
        cols = ", ".join(_safe_ident(k) for k in row)
        ph = self._placeholder()
        placeholders = ", ".join(ph for _ in row)
        sql = (
            f"INSERT INTO {_safe_ident(table)} ({cols}) "
            f"VALUES ({placeholders}) RETURNING id"
        )
        try:
            row_back = self.fetchone(sql, tuple(row.values()))
        except Exception:
            # Table without an `id` column — fall back to plain INSERT.
            sql = (
                f"INSERT INTO {_safe_ident(table)} ({cols}) "
                f"VALUES ({placeholders})"
            )
            self.execute(sql, tuple(row.values()))
            return None
        return row_back.get("id") if row_back else None

    def update(self, table: str, row: dict, where: dict) -> int:
        """UPDATE matching rows. Returns rows affected. WHERE is mandatory."""
        if not row:
            raise ValueError("update() requires a non-empty row dict")
        if not where:
            # We require an explicit where to avoid the famous footgun.
            raise ValueError("update() requires a non-empty where dict")
        ph = self._placeholder()
        set_clause = ", ".join(f"{_safe_ident(k)} = {ph}" for k in row)
        where_clause = " AND ".join(f"{_safe_ident(k)} = {ph}" for k in where)
        sql = f"UPDATE {_safe_ident(table)} SET {set_clause} WHERE {where_clause}"
        params = tuple(row.values()) + tuple(where.values())
        self.execute(sql, params)
        return self._rowcount()

    def delete(self, table: str, where: dict) -> int:
        """DELETE matching rows. Returns rows affected. WHERE is mandatory."""
        if not where:
            raise ValueError("delete() requires a non-empty where dict")
        ph = self._placeholder()
        where_clause = " AND ".join(f"{_safe_ident(k)} = {ph}" for k in where)
        sql = f"DELETE FROM {_safe_ident(table)} WHERE {where_clause}"
        self.execute(sql, tuple(where.values()))
        return self._rowcount()

    def upsert(self, table: str, row: dict, conflict_keys: Sequence[str]) -> None:
        """INSERT or update on conflict.

        Uses the standard ``INSERT ... ON CONFLICT (k) DO UPDATE SET ...``
        syntax supported by SQLite ≥3.24 and all PostgreSQL. Update set excludes
        the conflict keys themselves (they're the identity).
        """
        if not row:
            raise ValueError("upsert() requires a non-empty row dict")
        if not conflict_keys:
            raise ValueError("upsert() requires at least one conflict key")
        cks = set(conflict_keys)
        cols = list(row.keys())
        for k in cks:
            if k not in row:
                raise ValueError(f"conflict key {k!r} not present in row")
        update_cols = [c for c in cols if c not in cks]
        ph = self._placeholder()
        placeholders = ", ".join(ph for _ in cols)
        col_list = ", ".join(_safe_ident(c) for c in cols)
        conflict_list = ", ".join(_safe_ident(c) for c in conflict_keys)
        if update_cols:
            update_clause = ", ".join(
                f"{_safe_ident(c)} = excluded.{_safe_ident(c)}" for c in update_cols
            )
            sql = (
                f"INSERT INTO {_safe_ident(table)} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_list}) DO UPDATE SET {update_clause}"
            )
        else:
            # Row is entirely conflict keys — nothing to update on conflict.
            sql = (
                f"INSERT INTO {_safe_ident(table)} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_list}) DO NOTHING"
            )
        self.execute(sql, tuple(row.values()))

    # ─── migrations (default impl works for both backends) ────────────────

    _MIGRATION_TABLE = "_schema_migrations"

    def migrate(self, migrations_dir: Path) -> list[str]:
        """Apply pending .sql migrations in order. Idempotent.

        Files in ``migrations_dir`` are read in lexicographic order. Each
        filename is recorded in ``_schema_migrations`` after a successful apply
        — re-running migrate() skips already-applied files. Use a numeric prefix
        like ``0001__init.sql``, ``0002__add_user_role.sql`` to control order.

        Each .sql file may contain multiple statements separated by ``;``.
        Per-statement errors abort the migration; the partial transaction is
        rolled back so the migrations table doesn't record a half-applied file.

        Returns the list of newly-applied migration filenames.
        """
        migrations_dir = Path(migrations_dir)
        if not migrations_dir.exists():
            logger.debug("migrations_dir %s does not exist; skipping", migrations_dir)
            return []

        # Ensure the tracking table exists. TEXT for the filename keeps both
        # sqlite and postgres happy.
        self.execute(
            f"CREATE TABLE IF NOT EXISTS {self._MIGRATION_TABLE} ("
            f"  filename TEXT PRIMARY KEY,"
            f"  sha256   TEXT NOT NULL,"
            f"  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            f")"
        )

        applied = {
            r["filename"]: r["sha256"]
            for r in self.fetchall(f"SELECT filename, sha256 FROM {self._MIGRATION_TABLE}")
        }

        newly_applied: list[str] = []
        for path in sorted(migrations_dir.glob("*.sql")):
            name = path.name
            sql_text = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
            if name in applied:
                if applied[name] != digest:
                    logger.warning(
                        "migration %s already applied with a DIFFERENT hash. "
                        "Refusing to re-apply. (Old: %s..., new: %s...)",
                        name, applied[name][:8], digest[:8],
                    )
                continue

            logger.info("applying migration: %s", name)
            try:
                with self.transaction() as tx:
                    for stmt in _split_sql_statements(sql_text):
                        if stmt.strip():
                            tx.execute(stmt)
                    ph = self._placeholder()
                    tx.execute(
                        f"INSERT INTO {self._MIGRATION_TABLE} (filename, sha256) VALUES ({ph}, {ph})",
                        (name, digest),
                    )
                newly_applied.append(name)
            except Exception as e:
                logger.error("migration %s failed: %s", name, e)
                raise

        if newly_applied:
            logger.info("applied %d migration(s): %s", len(newly_applied), ", ".join(newly_applied))
        return newly_applied


# ─── helpers ──────────────────────────────────────────────────────────────

# Identifier validator — only alphanumerics + underscore. Defends against
# SQL injection via table/column name interpolation (those can't be
# parameterized in standard SQL, so we have to validate them).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> str:
    """Validate a table/column identifier. Raises if it contains anything
    other than alphanumerics + underscore."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def _split_sql_statements(sql: str) -> list[str]:
    """Split a multi-statement SQL string on ``;``, respecting string literals
    and SQL comments. Adequate for migration files we control."""
    out: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                buf.append(ch)
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if not in_single and not in_double:
            if ch == "-" and nxt == "-":
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue

        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out
