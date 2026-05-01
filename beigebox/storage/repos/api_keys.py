"""
ApiKeyRepo — entity repo for the api_keys table.

Sits on top of BaseDB; callers inject the driver.  Knows nothing about how the
driver stores bytes — that's BaseDB's job.  Knows everything about what an API
key *is*: creation, verification (constant-time bcrypt), listing, revocation.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beigebox.storage.db.base import BaseDB

_DDL = """
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    name         TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used    TEXT,
    last_rotated TEXT,
    expires_at   TEXT,
    active       INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id  ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
"""

# BCrypt cost kept at 12 (matches sqlite_store.py).  Import is deferred so the
# module can be imported even without bcrypt installed in environments that
# don't use this repo.
_BCRYPT_ROUNDS = 12


def _bcrypt():
    try:
        import bcrypt
        return bcrypt
    except ImportError as e:
        raise ImportError(
            "bcrypt is required for ApiKeyRepo. Install: pip install bcrypt"
        ) from e


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ApiKeyRepo:
    """Per-entity repository for api_keys.

    Inject a BaseDB instance; the repo owns the schema, hashing rules, and
    access patterns for this table.  It does NOT own the connection lifecycle —
    callers create and close the db.
    """

    def __init__(self, db: "BaseDB") -> None:
        self._db = db

    def create_tables(self) -> None:
        """Idempotent DDL for api_keys (+ indexes)."""
        ph = self._db._placeholder()
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._db.execute(stmt)

    # ── write operations ───────────────────────────────────────────────────

    def create(self, user_id: str, name: str = "default") -> tuple[str, str]:
        """Create a new API key.  Returns (key_id, plain_key).

        The plain key is returned once and never stored.  Only the bcrypt hash
        is persisted.
        """
        bc = _bcrypt()
        key_id = str(uuid.uuid4())
        plain_key = secrets.token_urlsafe(32)
        key_hash = bc.hashpw(plain_key.encode(), bc.gensalt(rounds=_BCRYPT_ROUNDS)).decode()
        now = _now_utc()
        ph = self._db._placeholder()
        self._db.execute(
            f"INSERT INTO api_keys (id, user_id, key_hash, name, created_at) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
            (key_id, user_id, key_hash, name, now),
        )
        return key_id, plain_key

    def revoke(self, key_id: str, user_id: str) -> bool:
        """Deactivate a key.  Returns True if a row was updated."""
        ph = self._db._placeholder()
        self._db.execute(
            f"UPDATE api_keys SET active=0 WHERE id={ph} AND user_id={ph}",
            (key_id, user_id),
        )
        return self._db._rowcount() > 0

    # ── read operations ────────────────────────────────────────────────────

    def list_for_user(self, user_id: str) -> list[dict]:
        """Return all keys for a user (no hash — metadata only)."""
        ph = self._db._placeholder()
        return self._db.fetchall(
            f"SELECT id, name, created_at, last_used, active "
            f"FROM api_keys WHERE user_id={ph} ORDER BY created_at DESC",
            (user_id,),
        )

    def verify(self, plain_key: str) -> str | None:
        """Verify a plain-text key against stored bcrypt hashes.

        Returns the owning user_id on success, None on failure.  Uses full
        bcrypt comparison (constant-time) for every active, non-expired hash —
        same approach as the original sqlite_store.verify_api_key().

        An extra dummy bcrypt call is made when no keys are found so that the
        response time is indistinguishable whether or not any keys exist.
        """
        bc = _bcrypt()
        now = _now_utc()
        ph = self._db._placeholder()
        rows = self._db.fetchall(
            f"SELECT id, user_id, key_hash FROM api_keys "
            f"WHERE active=1 AND (expires_at IS NULL OR expires_at > {ph})",
            (now,),
        )

        for row in rows:
            try:
                if bc.checkpw(plain_key.encode(), row["key_hash"].encode()):
                    self._db.execute(
                        f"UPDATE api_keys SET last_used={ph} WHERE id={ph}",
                        (_now_utc(), row["id"]),
                    )
                    return row["user_id"]
            except ValueError:
                continue

        # Constant-time guard: always pay at least one bcrypt operation.
        # gensalt() returns bytes; don't .encode() it.
        try:
            bc.checkpw(plain_key.encode(), bc.gensalt(rounds=4))
        except (ValueError, Exception):
            pass

        return None
