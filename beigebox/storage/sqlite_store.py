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
    ttft_ms REAL DEFAULT NULL,
    custom_field_1 TEXT DEFAULT NULL,
    custom_field_2 TEXT DEFAULT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS operator_runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    query TEXT NOT NULL,
    history TEXT NOT NULL,          -- JSON array of past messages
    model TEXT NOT NULL,
    status TEXT DEFAULT 'running',  -- running, completed, error
    result TEXT,                    -- final answer or error message
    latency_ms INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    goal TEXT NOT NULL,
    targets TEXT NOT NULL,          -- JSON array of target URLs/resources
    model TEXT NOT NULL,
    max_rounds INTEGER DEFAULT 8,
    final_answer TEXT,
    total_rounds INTEGER DEFAULT 0,
    was_capped BOOLEAN DEFAULT 0,
    total_latency_ms INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    events_jsonl TEXT               -- newline-delimited JSON event log for replay
);

CREATE TABLE IF NOT EXISTS wire_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    event_type  TEXT NOT NULL,   -- message, tool_call, routing_decision, cache_hit, op_thought, etc.
    source      TEXT NOT NULL,   -- proxy, operator, harness, router, cache, classifier
    conv_id     TEXT,
    run_id      TEXT,
    turn_id     TEXT,
    tool_id     TEXT,
    model       TEXT,
    role        TEXT,
    content     TEXT,            -- truncated to 2000 chars
    meta        TEXT,            -- JSON blob for event-specific fields (score, elapsed_ms, etc.)
    misc1       TEXT,
    misc2       TEXT
);

-- Indexes chosen for the most common access patterns:
-- conversation_id: fetching all messages in a conversation (very frequent)
-- timestamp: recent conversations list, date-range queries for metrics
-- role: filtering assistant messages for latency/cost stats
-- harness/operator created_at: sorting runs list by recency
-- wire_events: cross-linking by conv/run/type
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp
    ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_role
    ON messages(role);
CREATE INDEX IF NOT EXISTS idx_operator_runs_created
    ON operator_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_harness_runs_created
    ON harness_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_wire_events_conv
    ON wire_events(conv_id);
CREATE INDEX IF NOT EXISTS idx_wire_events_run
    ON wire_events(run_id);
CREATE INDEX IF NOT EXISTS idx_wire_events_type
    ON wire_events(event_type);
CREATE INDEX IF NOT EXISTS idx_wire_events_ts
    ON wire_events(ts);
"""

# Migrations: append-only ALTER TABLE statements that add new columns to
# existing databases. Safe to re-run — OperationalError "duplicate column name"
# is silently swallowed. Never DROP or RENAME columns here; that would destroy
# data for existing users who upgrade without a full migration tool.
MIGRATIONS = [
    # v0.6
    "ALTER TABLE messages ADD COLUMN cost_usd REAL DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN latency_ms REAL DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN custom_field_1 TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN custom_field_2 TEXT DEFAULT NULL",
    # v0.7 — TTFT persistence
    "ALTER TABLE messages ADD COLUMN ttft_ms REAL DEFAULT NULL",
    # v0.8 — trajectory evaluation scores for operator runs
    "ALTER TABLE operator_runs ADD COLUMN score_json TEXT DEFAULT NULL",
    # v0.9 — structured wire events table (tap redesign)
    # CREATE TABLE is in CREATE_TABLES (IF NOT EXISTS), migrations only needed for
    # existing DBs that don't have the table yet — handled by _init_db CREATE_TABLES.
    # Index migrations are also safe (CREATE INDEX IF NOT EXISTS in CREATE_TABLES).
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
        # WAL mode allows concurrent readers while a write is in progress.
        # Without WAL, any write holds an exclusive lock that blocks all reads —
        # problematic for the web UI polling metrics while requests are flowing.
        conn.execute("PRAGMA journal_mode=WAL")
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

    def store_message(self, msg: Message, cost_usd: float | None = None, latency_ms: float | None = None, ttft_ms: float | None = None):
        """Store a single message. Creates conversation if needed."""
        self.ensure_conversation(msg.conversation_id, msg.timestamp)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO messages
                   (id, conversation_id, role, content, model, timestamp, token_count, cost_usd, latency_ms, ttft_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg.id, msg.conversation_id, msg.role, msg.content,
                 msg.model, msg.timestamp, msg.token_count, cost_usd, latency_ms, ttft_ms),
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

    def get_model_performance(self, days: int = 30, since: str | None = None) -> dict:
        """
        Return per-model latency and throughput stats for the given period.

        Args:
            days:  lookback window (ignored when since is set)
            since: ISO timestamp string — only include data after this point

        Returns:
            {
                "by_model": {
                    "<model>": {
                        "requests":           int,
                        "avg_latency_ms":     float,
                        "p50_latency_ms":     float,
                        "p90_latency_ms":     float,
                        "p95_latency_ms":     float,
                        "p99_latency_ms":     float,
                        "avg_ttft_ms":        float | None,
                        "avg_tokens":         float,
                        "avg_tokens_per_sec": float,
                        "total_cost_usd":     float,
                    }, ...
                },
                "days_queried": int,
            }
        """
        if since:
            ts_filter = since
            ts_clause = "AND timestamp > ?"
        else:
            ts_filter = f"-{days} days"
            ts_clause = "AND timestamp > datetime('now', ?)"

        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT model,
                          COUNT(*) as requests,
                          AVG(latency_ms) as avg_lat,
                          AVG(token_count) as avg_tok,
                          COALESCE(SUM(cost_usd), 0) as total_cost,
                          AVG(ttft_ms) as avg_ttft
                   FROM messages
                   WHERE role = 'assistant'
                     AND latency_ms IS NOT NULL
                     {ts_clause}
                   GROUP BY model
                   ORDER BY requests DESC""",
                (ts_filter,),
            ).fetchall()

            # Fetch per-row latency + ttft for percentiles — single query, group in Python
            detail_rows = conn.execute(
                f"""SELECT model, latency_ms, ttft_ms, token_count FROM messages
                   WHERE role = 'assistant'
                     AND latency_ms IS NOT NULL
                     {ts_clause}
                   ORDER BY model, latency_ms""",
                (ts_filter,),
            ).fetchall()
            perf_by_model: dict[str, list[tuple[float, float | None, int]]] = {}
            for r in detail_rows:
                perf_by_model.setdefault(r["model"], []).append(
                    (r["latency_ms"], r["ttft_ms"], r["token_count"] or 0)
                )

            # Requests per day (all models combined)
            req_day_rows = conn.execute(
                f"""SELECT DATE(timestamp) as day, COUNT(*) as requests
                   FROM messages
                   WHERE role = 'assistant'
                     AND latency_ms IS NOT NULL
                     {ts_clause}
                   GROUP BY DATE(timestamp)
                   ORDER BY day""",
                (ts_filter,),
            ).fetchall()
            requests_by_day = {r["day"]: r["requests"] for r in req_day_rows}

        def _pct(vals: list[float], p: float) -> float:
            if not vals:
                return 0.0
            idx = min(int(len(vals) * p / 100), len(vals) - 1)
            return round(vals[idx], 1)

        def _avg_tps(rows: list[tuple[float, float | None, int]]) -> float:
            """Tokens/sec using generation latency (total − TTFT) when available.

            TTFT (time-to-first-token) is the model-load + prefill phase — not
            generation. Subtracting it gives a cleaner measure of how fast the
            model actually decodes. Falls back to total latency when TTFT is
            missing (older rows before v0.7 migration).
            """
            rates = []
            for lat, ttft, tok in rows:
                if tok <= 0:
                    continue
                gen_ms = (lat - ttft) if (ttft is not None and lat > ttft) else lat
                if gen_ms > 0:
                    rates.append(tok / (gen_ms / 1000.0))
            return round(sum(rates) / len(rates), 1) if rates else 0.0

        by_model = {}
        for row in rows:
            model = row["model"]
            perf = perf_by_model.get(model, [])
            lats = [p[0] for p in perf]
            avg_ttft = row["avg_ttft"]
            by_model[model] = {
                "requests":           row["requests"],
                "avg_latency_ms":     round(row["avg_lat"] or 0, 1),
                "p50_latency_ms":     _pct(lats, 50),
                "p90_latency_ms":     _pct(lats, 90),
                "p95_latency_ms":     _pct(lats, 95),
                "p99_latency_ms":     _pct(lats, 99),
                "avg_ttft_ms":        round(avg_ttft, 1) if avg_ttft is not None else None,
                "avg_tokens":         round(row["avg_tok"] or 0, 1),
                "avg_tokens_per_sec": _avg_tps(perf),
                "total_cost_usd":     round(row["total_cost"] or 0, 6),
            }

        return {"by_model": by_model, "days_queried": days, "requests_by_day": requests_by_day}

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
                        timestamp, token_count, cost_usd, latency_ms, ttft_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        msg.get("ttft_ms"),
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

    def store_operator_run(self, run_id: str, query: str, history: list,
                          model: str, status: str = "running",
                          result: str = None, latency_ms: int = 0) -> None:
        """Store an operator run."""
        import time
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO operator_runs
                   (id, created_at, query, history, model, status, result, latency_ms, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    now,
                    query,
                    json.dumps(history),
                    model,
                    status,
                    result,
                    latency_ms,
                    now,
                ),
            )
        logger.debug("Stored operator run %s (query=%s, status=%s)", run_id, query[:50], status)

    def get_operator_run(self, run_id: str) -> dict | None:
        """Retrieve a stored operator run by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM operator_runs WHERE id = ?",
                (run_id,),
            ).fetchone()

        if not row:
            return None

        run = dict(row)
        run["history"] = json.loads(run["history"])
        return run

    def list_operator_runs(self, limit: int = 50) -> list[dict]:
        """List recent operator runs."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, created_at, query, model, status, latency_ms, updated_at
                   FROM operator_runs
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        return [dict(r) for r in rows]

    def update_operator_run_status(self, run_id: str, status: str, result: str = None,
                                   latency_ms: int = 0) -> None:
        """Update status of an operator run after completion."""
        import time
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """UPDATE operator_runs
                   SET status = ?, result = ?, latency_ms = ?, updated_at = ?
                   WHERE id = ?""",
                (status, result, latency_ms, now, run_id),
            )
        logger.debug("Updated operator run %s (status=%s)", run_id, status)

    def store_run_score(self, run_id: str, score_dict: dict) -> None:
        """Persist trajectory score JSON for an operator run."""
        import json as _json
        with self._connect() as conn:
            conn.execute(
                "UPDATE operator_runs SET score_json = ? WHERE id = ?",
                (_json.dumps(score_dict), run_id),
            )
        logger.debug("Stored trajectory score for run %s (score=%.1f)", run_id, score_dict.get("score", 0))

    # ─ Wire events (structured tap) ───────────────────────────────────────

    def log_wire_event(
        self,
        event_type: str,
        source: str,
        content: str = "",
        role: str = "",
        model: str = "",
        conv_id: str | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
        tool_id: str | None = None,
        meta: dict | None = None,
        misc1: str | None = None,
        misc2: str | None = None,
    ) -> None:
        """Write a structured wire event to the wire_events table."""
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        if len(content) > 2000:
            content = content[:1000] + f"\n\n[...{len(content) - 2000} chars truncated...]\n\n" + content[-1000:]
        meta_str = json.dumps(meta, ensure_ascii=False) if meta else None
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO wire_events
                       (ts, event_type, source, conv_id, run_id, turn_id, tool_id,
                        model, role, content, meta, misc1, misc2)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, event_type, source, conv_id, run_id, turn_id, tool_id,
                     model, role, content, meta_str, misc1, misc2),
                )
        except Exception as e:
            logger.warning("log_wire_event failed (%s/%s): %s", source, event_type, e)

    def get_wire_events(
        self,
        n: int = 100,
        event_type: str | None = None,
        source: str | None = None,
        conv_id: str | None = None,
        run_id: str | None = None,
        role: str | None = None,
    ) -> list[dict]:
        """Query wire events with optional filters. Returns newest-first."""
        clauses = []
        params: list = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if conv_id:
            clauses.append("conv_id = ?")
            params.append(conv_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if role:
            clauses.append("role = ?")
            params.append(role)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(n)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM wire_events {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        events = []
        for row in rows:
            e = dict(row)
            if e.get("meta"):
                try:
                    e["meta"] = json.loads(e["meta"])
                except Exception:
                    pass
            events.append(e)
        return events

