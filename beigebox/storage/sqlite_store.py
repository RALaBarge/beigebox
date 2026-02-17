"""
SQLite storage for raw conversation data.
This is the source of truth - every message, every timestamp, every model.
Single portable file. Query with SQL. Export to JSON.
"""

import sqlite3
import json
import logging
from pathlib import Path
from contextlib import contextmanager

from beigebox.storage.models import Message

logger = logging.getLogger(__name__)

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp
    ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_role
    ON messages(role);
"""


class SQLiteStore:
    """Thread-safe SQLite conversation store."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(CREATE_TABLES)
        logger.info("SQLite store initialized at %s", self.db_path)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_conversation(self, conversation_id: str, created_at: str):
        """Create conversation record if it doesn't exist."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations (id, created_at) VALUES (?, ?)",
                (conversation_id, created_at),
            )

    def store_message(self, msg: Message):
        """Store a single message. Creates conversation if needed."""
        self.ensure_conversation(msg.conversation_id, msg.timestamp)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO messages
                   (id, conversation_id, role, content, model, timestamp, token_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (msg.id, msg.conversation_id, msg.role, msg.content,
                 msg.model, msg.timestamp, msg.token_count),
            )
        logger.debug("Stored message %s (role=%s, conv=%s)", msg.id, msg.role, msg.conversation_id)

    def get_conversation(self, conversation_id: str) -> list[dict]:
        """Retrieve all messages for a conversation in order."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp",
                (conversation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_conversations(self, limit: int = 20) -> list[dict]:
        """Get most recent conversations with their last message."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.id, c.created_at,
                          (SELECT content FROM messages m
                           WHERE m.conversation_id = c.id
                           ORDER BY m.timestamp DESC LIMIT 1) as last_message,
                          (SELECT COUNT(*) FROM messages m
                           WHERE m.conversation_id = c.id) as message_count
                   FROM conversations c
                   ORDER BY c.created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_all_json(self) -> list[dict]:
        """Export all conversations in OpenAI-compatible format."""
        with self._connect() as conn:
            conversations = conn.execute(
                "SELECT id FROM conversations ORDER BY created_at"
            ).fetchall()

        result = []
        for conv in conversations:
            messages = self.get_conversation(conv["id"])
            result.append({
                "conversation_id": conv["id"],
                "messages": [
                    {"role": m["role"], "content": m["content"],
                     "model": m["model"], "timestamp": m["timestamp"]}
                    for m in messages
                ],
            })
        return result

    def get_stats(self) -> dict:
        """Return stats about stored data including token usage."""
        with self._connect() as conn:
            conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            user_count = conn.execute("SELECT COUNT(*) FROM messages WHERE role='user'").fetchone()[0]
            asst_count = conn.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]

            # Token stats
            total_tokens = conn.execute(
                "SELECT COALESCE(SUM(token_count), 0) FROM messages"
            ).fetchone()[0]
            user_tokens = conn.execute(
                "SELECT COALESCE(SUM(token_count), 0) FROM messages WHERE role='user'"
            ).fetchone()[0]
            asst_tokens = conn.execute(
                "SELECT COALESCE(SUM(token_count), 0) FROM messages WHERE role='assistant'"
            ).fetchone()[0]

            # Per-model breakdown
            model_rows = conn.execute(
                """SELECT model,
                          COUNT(*) as messages,
                          COALESCE(SUM(token_count), 0) as tokens
                   FROM messages
                   WHERE model != ''
                   GROUP BY model
                   ORDER BY messages DESC"""
            ).fetchall()
            models = {
                row["model"]: {"messages": row["messages"], "tokens": row["tokens"]}
                for row in model_rows
            }

        return {
            "conversations": conv_count,
            "messages": msg_count,
            "user_messages": user_count,
            "assistant_messages": asst_count,
            "tokens": {
                "total": total_tokens,
                "user": user_tokens,
                "assistant": asst_tokens,
            },
            "models": models,
        }
