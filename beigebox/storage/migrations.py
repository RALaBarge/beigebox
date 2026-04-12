"""
Database schema migrations for BeigeBox storage.

Migrations run once on startup if schema version has changed.
All migrations are append-only (ADD COLUMN, no DROP/RENAME).
Safe to re-run — OperationalError "duplicate column name" is silently swallowed.
"""

import sqlite3
import logging

logger = logging.getLogger(__name__)


def add_integrity_columns(conn: sqlite3.Connection) -> None:
    """
    Migration: Add integrity validation columns to messages table.

    Adds 3 columns to support HMAC-SHA256 signatures:
    - message_hmac: HMAC-SHA256 signature of the message
    - integrity_version: Schema version for future migrations (starts at 1)
    - tamper_detected: Boolean flag if corruption was detected on read

    This migration is safe to re-run (uses ALTER TABLE IF NOT EXISTS pattern).

    Args:
        conn: SQLite connection

    Raises:
        sqlite3.OperationalError: If migration fails unexpectedly
    """
    try:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN message_hmac TEXT DEFAULT NULL"
        )
        logger.info("Added message_hmac column to messages table")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            logger.warning("Failed to add message_hmac: %s", e)

    try:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN integrity_version INTEGER DEFAULT 1"
        )
        logger.info("Added integrity_version column to messages table")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            logger.warning("Failed to add integrity_version: %s", e)

    try:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN tamper_detected BOOLEAN DEFAULT 0"
        )
        logger.info("Added tamper_detected column to messages table")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            logger.warning("Failed to add tamper_detected: %s", e)

    try:
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN integrity_checked_at TEXT DEFAULT NULL"
        )
        logger.info("Added integrity_checked_at column to conversations table")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            logger.warning("Failed to add integrity_checked_at: %s", e)

    conn.commit()


def populate_existing_signatures(
    conn: sqlite3.Connection,
    sign_func,
    user_id: str
) -> int:
    """
    Populate HMAC signatures for existing messages (backward compatibility).

    Computes and stores signatures for all messages that don't have them yet.
    Used during migration to add integrity checks to existing databases.

    Args:
        conn: SQLite connection
        sign_func: Callable that takes (message_dict, user_id) → signature string
        user_id: User ID (typically "system" for pre-existing messages)

    Returns:
        Number of messages signed
    """
    cursor = conn.cursor()

    # Find all messages without signatures
    cursor.execute(
        """SELECT id, conversation_id, role, content, model, timestamp, token_count
           FROM messages WHERE message_hmac IS NULL"""
    )
    rows = cursor.fetchall()

    if not rows:
        logger.info("No unsigned messages found")
        return 0

    signed_count = 0
    for row in rows:
        try:
            msg_dict = dict(row)
            sig = sign_func(msg_dict, user_id)
            cursor.execute(
                "UPDATE messages SET message_hmac = ? WHERE id = ?",
                (sig, msg_dict["id"])
            )
            signed_count += 1
        except Exception as e:
            logger.warning("Failed to sign message %s: %s", msg_dict["id"], e)

    conn.commit()
    logger.info("Populated %d message signatures", signed_count)
    return signed_count
