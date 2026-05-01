"""
UserRepo — entity repo for the users table.

Sits on top of BaseDB; callers inject the driver. Holds the schema, the
provider+sub uniqueness constraint, and the three access patterns
(upsert, get, password update).

Migrated out of SQLiteStore on 2026-05-01; see project memory
"BeigeBox v3 / beigebox-security" for the demolition path.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beigebox.storage.db.base import BaseDB

logger = logging.getLogger(__name__)


_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    sub           TEXT NOT NULL,
    email         TEXT NOT NULL,
    name          TEXT NOT NULL,
    picture       TEXT DEFAULT '',
    password_hash TEXT DEFAULT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(provider, sub)
);
CREATE INDEX IF NOT EXISTS idx_users_provider_sub ON users(provider, sub);
"""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class UserRepo:
    """Per-entity repository for users."""

    def __init__(self, db: "BaseDB") -> None:
        self._db = db

    def create_tables(self) -> None:
        """Idempotent DDL for users (+ indexes)."""
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._db.execute(stmt)

    # ── write operations ───────────────────────────────────────────────────

    def upsert(
        self,
        provider: str,
        sub: str,
        email: str,
        name: str,
        picture: str = "",
    ) -> str:
        """Insert or update a user row; return the stable user_id UUID.

        provider+sub is the natural key — second call with the same pair
        updates email/name/picture/last_seen on the existing row.
        """
        ph = self._db._placeholder()
        now = _now_utc()
        existing = self._db.fetchone(
            f"SELECT id FROM users WHERE provider={ph} AND sub={ph}",
            (provider, sub),
        )
        if existing:
            user_id = existing["id"]
            self._db.execute(
                f"UPDATE users SET email={ph}, name={ph}, picture={ph}, last_seen={ph} WHERE id={ph}",
                (email, name, picture, now, user_id),
            )
            return user_id

        user_id = str(uuid.uuid4())
        self._db.execute(
            f"INSERT INTO users (id, provider, sub, email, name, picture, created_at, last_seen) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
            (user_id, provider, sub, email, name, picture, now, now),
        )
        return user_id

    def update_password(self, user_id: str, password_hash: str) -> bool:
        """Update a user's password hash. Returns True on success, False on error."""
        ph = self._db._placeholder()
        try:
            self._db.execute(
                f"UPDATE users SET password_hash = {ph} WHERE id = {ph}",
                (password_hash, user_id),
            )
            return True
        except Exception as e:
            logger.error("Failed to update password for %s: %s", user_id, e)
            return False

    # ── read operations ────────────────────────────────────────────────────

    def get(self, user_id: str) -> dict | None:
        """Return one user row by id, or None."""
        ph = self._db._placeholder()
        return self._db.fetchone(
            f"SELECT * FROM users WHERE id = {ph}",
            (user_id,),
        )
