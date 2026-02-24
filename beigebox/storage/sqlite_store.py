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
    cost_usd REAL DEFAULT NULL,
    latency_ms REAL DEFAULT NULL,
    custom_field_1 TEXT DEFAULT NULL,
    custom_field_2 TEXT DEFAULT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS harness_runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    goal TEXT NOT NULL,
    targets TEXT NOT NULL,
    model TEXT NOT NULL,
    max_rounds INTEGER DEFAULT 8,
    final_answer TEXT,
    total_rounds INTEGER DEFAULT 0,
    was_capped BOOLEAN DEFAULT 0,
    total_latency_ms INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    events_jsonl TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp
    ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_role
    ON messages(role);
CREATE INDEX IF NOT EXISTS idx_harness_runs_created
    ON harness_runs(created_at);
"""

# v0.6 migration: add columns to existing databases (safe to re-run)
MIGRATIONS = [
    "ALTER TABLE messages ADD COLUMN cost_usd REAL DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN latency_ms REAL DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN custom_field_1 TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN custom_field_2 TEXT DEFAULT NULL",
]


class SQLiteStore:
    """Thread-safe SQLite conversation store."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(CREATE_TABLES)
            # Run migrations for existing databases (safe if columns already exist)
            for migration in MIGRATIONS:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        logger.warning("Migration skipped: %s", e)
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

    def store_message(self, msg: Message, cost_usd: float | None = None, latency_ms: float | None = None):
        """Store a single message. Creates conversation if needed."""
        self.ensure_conversation(msg.conversation_id, msg.timestamp)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO messages
                   (id, conversation_id, role, content, model, timestamp, token_count, cost_usd, latency_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg.id, msg.conversation_id, msg.role, msg.content,
                 msg.model, msg.timestamp, msg.token_count, cost_usd, latency_ms),
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

    def get_model_performance(self, days: int = 30) -> dict:
        """
        Return per-model latency and throughput stats for the given period.

        Returns:
            {
                "by_model": {
                    "<model>": {
                        "requests":       int,
                        "avg_latency_ms": float,
                        "p50_latency_ms": float,
                        "p95_latency_ms": float,
                        "avg_tokens":     float,
                        "total_cost_usd": float,
                    }, ...
                },
                "days_queried": int,
            }
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT model,
                          COUNT(*) as requests,
                          AVG(latency_ms) as avg_lat,
                          AVG(token_count) as avg_tok,
                          COALESCE(SUM(cost_usd), 0) as total_cost
                   FROM messages
                   WHERE role = 'assistant'
                     AND latency_ms IS NOT NULL
                     AND timestamp > datetime('now', ?)
                   GROUP BY model
                   ORDER BY requests DESC""",
                (f"-{days} days",),
            ).fetchall()

            latencies_by_model: dict[str, list[float]] = {}
            for row in rows:
                model = row["model"]
                lat_rows = conn.execute(
                    """SELECT latency_ms FROM messages
                       WHERE role = 'assistant'
                         AND model = ?
                         AND latency_ms IS NOT NULL
                         AND timestamp > datetime('now', ?)
                       ORDER BY latency_ms""",
                    (model, f"-{days} days"),
                ).fetchall()
                latencies_by_model[model] = [r["latency_ms"] for r in lat_rows]

        def _pct(vals: list[float], p: float) -> float:
            if not vals:
                return 0.0
            idx = min(int(len(vals) * p / 100), len(vals) - 1)
            return round(vals[idx], 1)

        by_model = {}
        for row in rows:
            model = row["model"]
            lats = latencies_by_model.get(model, [])
            by_model[model] = {
                "requests":       row["requests"],
                "avg_latency_ms": round(row["avg_lat"] or 0, 1),
                "p50_latency_ms": _pct(lats, 50),
                "p95_latency_ms": _pct(lats, 95),
                "avg_tokens":     round(row["avg_tok"] or 0, 1),
                "total_cost_usd": round(row["total_cost"] or 0, 6),
            }

        return {"by_model": by_model, "days_queried": days}

    def fork_conversation(
        self,
        source_conv_id: str,
        new_conv_id: str,
        branch_at: int | None = None,
    ) -> int:
        """
        Fork a conversation into a new one.

        Copies messages from source_conv_id into new_conv_id.
        If branch_at is given, only messages 0..branch_at (inclusive) are copied.
        Returns the number of messages copied.
        """
        messages = self.get_conversation(source_conv_id)
        if branch_at is not None:
            messages = messages[: branch_at + 1]
        if not messages:
            return 0

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.ensure_conversation(new_conv_id, now)

        with self._connect() as conn:
            for msg in messages:
                from uuid import uuid4
                conn.execute(
                    """INSERT INTO messages
                       (id, conversation_id, role, content, model,
                        timestamp, token_count, cost_usd, latency_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        uuid4().hex,
                        new_conv_id,
                        msg["role"],
                        msg["content"],
                        msg.get("model", ""),
                        msg["timestamp"],
                        msg.get("token_count", 0),
                        msg.get("cost_usd"),
                        msg.get("latency_ms"),
                    ),
                )
        logger.info(
            "Forked %d messages from %s → %s (branch_at=%s)",
            len(messages), source_conv_id, new_conv_id, branch_at,
        )
        return len(messages)

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

    def export_jsonl(self, model_filter: str | None = None) -> list[dict]:
        """
        Export conversations as JSONL-style dicts (one per conversation).
        Each entry: {"messages": [{"role": ..., "content": ...}, ...]}
        Suitable for fine-tuning with most frameworks.
        """
        raw = self.export_all_json()
        result = []
        for conv in raw:
            msgs = [
                {"role": m["role"], "content": m["content"]}
                for m in conv["messages"]
                if m["role"] in ("user", "assistant")
                and (not model_filter or m.get("model") == model_filter)
            ]
            # Must have at least one user+assistant pair
            roles = {m["role"] for m in msgs}
            if "user" in roles and "assistant" in roles:
                result.append({"messages": msgs})
        return result

    def export_alpaca(self, model_filter: str | None = None) -> list[dict]:
        """
        Export as Alpaca-format instruction pairs.
        Each user message + following assistant message becomes one record:
          {"instruction": <user>, "input": "", "output": <assistant>}
        """
        raw = self.export_all_json()
        result = []
        for conv in raw:
            msgs = [
                m for m in conv["messages"]
                if m["role"] in ("user", "assistant")
                and (not model_filter or m.get("model") == model_filter)
            ]
            # Walk pairs
            i = 0
            while i < len(msgs) - 1:
                if msgs[i]["role"] == "user" and msgs[i + 1]["role"] == "assistant":
                    result.append({
                        "instruction": msgs[i]["content"],
                        "input": "",
                        "output": msgs[i + 1]["content"],
                    })
                    i += 2
                else:
                    i += 1
        return result

    def export_sharegpt(self, model_filter: str | None = None) -> list[dict]:
        """
        Export as ShareGPT format.
        Each conversation becomes one record:
          {"conversations": [{"from": "human"|"gpt", "value": ...}, ...]}
        """
        role_map = {"user": "human", "assistant": "gpt", "system": "system"}
        raw = self.export_all_json()
        result = []
        for conv in raw:
            msgs = [
                {"from": role_map.get(m["role"], m["role"]), "value": m["content"]}
                for m in conv["messages"]
                if m["role"] in ("user", "assistant", "system")
                and (not model_filter or m.get("model") == model_filter)
            ]
            roles = {m["from"] for m in msgs}
            if "human" in roles and "gpt" in roles:
                result.append({
                    "id": conv["conversation_id"],
                    "conversations": msgs,
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
                          COALESCE(SUM(token_count), 0) as tokens,
                          COALESCE(SUM(cost_usd), 0) as cost
                   FROM messages
                   WHERE model != ''
                   GROUP BY model
                   ORDER BY messages DESC"""
            ).fetchall()
            models = {
                row["model"]: {"messages": row["messages"], "tokens": row["tokens"], "cost_usd": row["cost"]}
                for row in model_rows
            }

            # Total cost
            total_cost = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM messages"
            ).fetchone()[0]

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
            "cost_usd": total_cost,
            "models": models,
        }

    # ─ Harness orchestration run storage ──────────────────────────────────

    def store_harness_run(self, run_dict: dict) -> None:
        """Store a harness orchestration run."""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO harness_runs
                   (id, created_at, goal, targets, model, max_rounds, final_answer,
                    total_rounds, was_capped, total_latency_ms, error_count, events_jsonl)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_dict["id"],
                    run_dict["created_at"],
                    run_dict["goal"],
                    json.dumps(run_dict["targets"]),
                    run_dict["model"],
                    run_dict["max_rounds"],
                    run_dict.get("final_answer", ""),
                    run_dict["total_rounds"],
                    run_dict.get("was_capped", False),
                    run_dict["total_latency_ms"],
                    run_dict["error_count"],
                    run_dict["events_jsonl"],
                ),
            )
        logger.debug("Stored harness run %s (goal=%s)", run_dict["id"], run_dict["goal"][:50])

    def get_harness_run(self, run_id: str) -> dict | None:
        """Retrieve a stored harness run by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM harness_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        
        if not row:
            return None
        
        run = dict(row)
        run["targets"] = json.loads(run["targets"])
        run["events"] = [
            json.loads(line) for line in run["events_jsonl"].split("\n") if line.strip()
        ]
        return run

    def list_harness_runs(self, limit: int = 10) -> list[dict]:
        """List recent harness runs."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, created_at, goal, total_rounds, total_latency_ms,
                          error_count, was_capped
                   FROM harness_runs
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        
        return [dict(r) for r in rows]
