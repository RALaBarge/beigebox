"""
Migration v1.2 — Memory Integrity Validation columns.

Adds HMAC-SHA256 signature tracking to the messages table and
integrity audit timestamps to the conversations table.

New columns:
  messages.message_hmac         TEXT    — HMAC-SHA256 hex digest (64 chars)
  messages.integrity_version    INTEGER — schema version for future upgrades
  messages.tamper_detected      BOOLEAN — set to 1 if verification failed
  conversations.integrity_checked_at TEXT — ISO timestamp of last audit

All columns are nullable / have safe defaults for backwards compatibility.
Existing rows get NULL/0 which means "not yet signed" — the re-signing
pass (populate_existing_signatures) can be run separately.

Safe to re-run: each ALTER TABLE catches "duplicate column" errors.
"""

import sqlite3
import logging

logger = logging.getLogger(__name__)


# Individual column additions — each wrapped in try/except so the migration
# is idempotent.
COLUMN_MIGRATIONS = [
    ("messages", "message_hmac", "TEXT DEFAULT NULL"),
    ("messages", "integrity_version", "INTEGER DEFAULT 1"),
    ("messages", "tamper_detected", "BOOLEAN DEFAULT 0"),
    ("conversations", "integrity_checked_at", "TEXT DEFAULT NULL"),
]

# Index to speed up "find unsigned messages" queries during re-signing.
INDEX_MIGRATIONS = [
    (
        "idx_messages_hmac_null",
        "CREATE INDEX IF NOT EXISTS idx_messages_hmac_null "
        "ON messages(message_hmac) WHERE message_hmac IS NULL",
    ),
    (
        "idx_messages_tamper",
        "CREATE INDEX IF NOT EXISTS idx_messages_tamper "
        "ON messages(tamper_detected) WHERE tamper_detected = 1",
    ),
]


def upgrade(conn: sqlite3.Connection) -> dict:
    """
    Run the v1.2 migration.

    Args:
        conn: Open SQLite connection (caller manages transaction).

    Returns:
        Dict with migration results:
        {
            "columns_added": list[str],
            "columns_skipped": list[str],
            "indexes_created": list[str],
        }
    """
    columns_added = []
    columns_skipped = []
    indexes_created = []

    for table, column, definition in COLUMN_MIGRATIONS:
        fqn = f"{table}.{column}"
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            columns_added.append(fqn)
            logger.info("Migration v1.2: added %s", fqn)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                columns_skipped.append(fqn)
            else:
                logger.warning("Migration v1.2: failed to add %s: %s", fqn, e)
                raise

    for idx_name, idx_sql in INDEX_MIGRATIONS:
        try:
            conn.execute(idx_sql)
            indexes_created.append(idx_name)
            logger.info("Migration v1.2: created index %s", idx_name)
        except sqlite3.OperationalError as e:
            logger.warning("Migration v1.2: index %s failed: %s", idx_name, e)

    conn.commit()

    return {
        "columns_added": columns_added,
        "columns_skipped": columns_skipped,
        "indexes_created": indexes_created,
    }


def resign_unsigned(
    conn: sqlite3.Connection,
    sign_func,
    user_id: str = "system",
    batch_size: int = 500,
) -> int:
    """
    Populate HMAC signatures for existing unsigned messages.

    This is the backwards-compatibility pass: after upgrading the schema,
    run this once to sign all pre-existing messages so they pass future
    integrity checks.

    Args:
        conn: Open SQLite connection.
        sign_func: Callable(msg_dict, user_id) -> str (HMAC hex digest).
        user_id: User ID to use for signing (default "system").
        batch_size: Commit every N rows to avoid long transactions.

    Returns:
        Number of messages signed.
    """
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, conversation_id, role, content, model, timestamp, token_count
           FROM messages
           WHERE message_hmac IS NULL
           ORDER BY timestamp"""
    )

    signed = 0
    batch = []

    for row in cursor:
        msg_dict = {
            "id": row[0],
            "conversation_id": row[1],
            "role": row[2],
            "content": row[3],
            "model": row[4],
            "timestamp": row[5],
            "token_count": row[6],
        }
        try:
            sig = sign_func(msg_dict, user_id)
            batch.append((sig, msg_dict["id"]))
            signed += 1

            if len(batch) >= batch_size:
                conn.executemany(
                    "UPDATE messages SET message_hmac = ? WHERE id = ?",
                    batch,
                )
                conn.commit()
                batch.clear()
        except Exception as e:
            logger.warning("resign_unsigned: failed to sign %s: %s", msg_dict["id"], e)

    # Flush remaining batch
    if batch:
        conn.executemany(
            "UPDATE messages SET message_hmac = ? WHERE id = ?",
            batch,
        )
        conn.commit()

    logger.info("resign_unsigned: signed %d messages", signed)
    return signed
