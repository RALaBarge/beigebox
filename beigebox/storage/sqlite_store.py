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
from typing import Any, Dict, Optional
from datetime import datetime, timezone

from beigebox.storage.models import Message
from beigebox.security.memory_integrity import ConversationIntegrityValidator, IntegrityAuditLog
from beigebox.security.key_management import KeyManager

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
    -- v1.4 — normalizer-captured telemetry (set by capture.CaptureFanout)
    reasoning_text TEXT DEFAULT NULL,           -- full chain-of-thought for reasoning models
    tool_calls_json TEXT DEFAULT NULL,          -- JSON-encoded tool_calls array
    finish_reason TEXT DEFAULT NULL,            -- stop / length / tool_calls / error / aborted
    prompt_tokens INTEGER DEFAULT NULL,         -- split out from token_count
    completion_tokens INTEGER DEFAULT NULL,
    reasoning_tokens INTEGER DEFAULT NULL,
    request_transforms_json TEXT DEFAULT NULL,  -- JSON list of normalizer transforms that fired
    normalize_errors_json TEXT DEFAULT NULL,    -- JSON list of non-fatal normalize errors
    request_id TEXT DEFAULT NULL,               -- upstream provider's request id when known
    capture_outcome TEXT DEFAULT NULL,          -- ok / upstream_error / stream_aborted / client_disconnect
    error_kind TEXT DEFAULT NULL,               -- mirrors capture_outcome when != ok, else NULL
    error_message TEXT DEFAULT NULL,            -- str(exc) when capture failed
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
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
-- wire_events: cross-linking by conv/run/type
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp
    ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_role
    ON messages(role);
CREATE INDEX IF NOT EXISTS idx_wire_events_conv
    ON wire_events(conv_id);
CREATE INDEX IF NOT EXISTS idx_wire_events_run
    ON wire_events(run_id);
CREATE INDEX IF NOT EXISTS idx_wire_events_type
    ON wire_events(event_type);
CREATE INDEX IF NOT EXISTS idx_wire_events_ts
    ON wire_events(ts);

CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    provider   TEXT NOT NULL,
    sub        TEXT NOT NULL,       -- provider-unique subject ID
    email      TEXT NOT NULL,
    name       TEXT NOT NULL,
    picture    TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(provider, sub)
);
CREATE INDEX IF NOT EXISTS idx_users_provider_sub ON users(provider, sub);

CREATE TABLE IF NOT EXISTS quarantined_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    document_id TEXT NOT NULL,
    embedding_hash TEXT,
    confidence REAL NOT NULL,
    reason TEXT,
    detector_method TEXT DEFAULT 'magnitude'
);
CREATE INDEX IF NOT EXISTS idx_quarantined_embeddings_timestamp
    ON quarantined_embeddings(timestamp);
CREATE INDEX IF NOT EXISTS idx_quarantined_embeddings_document
    ON quarantined_embeddings(document_id);
CREATE INDEX IF NOT EXISTS idx_quarantined_embeddings_confidence
    ON quarantined_embeddings(confidence);

CREATE TABLE IF NOT EXISTS api_keys (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,   -- SHA256 of actual key (never store plaintext)
    name        TEXT,                   -- user-given name for the key
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used   TEXT,
    last_rotated TEXT,
    expires_at  TEXT,                   -- optional expiration
    active      INTEGER DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
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
    # v0.9 — structured wire events table (tap redesign)
    # CREATE TABLE is in CREATE_TABLES (IF NOT EXISTS), migrations only needed for
    # existing DBs that don't have the table yet — handled by _init_db CREATE_TABLES.
    # Index migrations are also safe (CREATE INDEX IF NOT EXISTS in CREATE_TABLES).
    # v1.0 — web auth: user tracking
    "ALTER TABLE conversations ADD COLUMN user_id TEXT DEFAULT NULL",
    # v1.2 — memory integrity validation columns
    "ALTER TABLE messages ADD COLUMN message_hmac TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN integrity_version INTEGER DEFAULT 1",
    "ALTER TABLE messages ADD COLUMN tamper_detected BOOLEAN DEFAULT 0",
    "ALTER TABLE conversations ADD COLUMN integrity_checked_at TEXT DEFAULT NULL",
    # v1.3 — password hash for simple password auth
    "ALTER TABLE users ADD COLUMN password_hash TEXT DEFAULT NULL",
    # v1.4 — normalizer-captured telemetry. Every chat completion gets one row
    # for the request batch and one for the response, populated by
    # beigebox.capture.CaptureFanout. All nullable so legacy rows (and rows
    # written by callers that bypass the fanout) keep working.
    "ALTER TABLE messages ADD COLUMN reasoning_text TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN tool_calls_json TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN finish_reason TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN prompt_tokens INTEGER DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN completion_tokens INTEGER DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN reasoning_tokens INTEGER DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN request_transforms_json TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN normalize_errors_json TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN request_id TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN capture_outcome TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN error_kind TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN error_message TEXT DEFAULT NULL",
]


class SQLiteStore:
    """Thread-safe SQLite conversation store with integrity validation."""

    def __init__(self, db_path: str, integrity_config: Optional[Dict] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize integrity validator if enabled
        self.integrity_validator: Optional[ConversationIntegrityValidator] = None
        self.integrity_mode: str = "log_only"
        self._init_integrity(integrity_config or {})

        self._init_db()

    @staticmethod
    def _extract_signable_fields(msg: dict) -> dict:
        """
        Extract only the fields that are signed for integrity verification.

        These must match the fields signed in store_message() to avoid mismatches.

        Args:
            msg: Full message dict from database

        Returns:
            Dict with only signable fields
        """
        signable_fields = {
            "id", "conversation_id", "role", "content", "model",
            "timestamp", "token_count"
        }
        return {k: v for k, v in msg.items() if k in signable_fields}

    def _init_integrity(self, integrity_config: Dict) -> None:
        """
        Initialize integrity validation if enabled.

        Args:
            integrity_config: Config dict with keys: enabled, mode, key_source, key_path, dev_mode
        """
        # Default to disabled when no config is provided (empty dict).
        # Explicit {"enabled": true} is required to activate integrity.
        if not integrity_config or not integrity_config.get("enabled", False):
            logger.info("Memory integrity validation disabled")
            return

        try:
            mode = integrity_config.get("mode", "log_only")
            key_source = integrity_config.get("key_source", "env")
            key_path = integrity_config.get("key_path", "~/.beigebox/memory.key")
            dev_mode = integrity_config.get("dev_mode", False)

            # Expand ~ in path
            if key_path.startswith("~"):
                key_path = str(Path(key_path).expanduser())

            # Load key from configured source
            key = KeyManager.load_key(
                key_source=key_source,
                key_path=key_path,
                dev_mode=dev_mode
            )

            if key is None:
                logger.warning(
                    "Memory integrity key not available (dev_mode=%s)", dev_mode
                )
                return

            # Initialize validator
            self.integrity_validator = ConversationIntegrityValidator(key)
            self.integrity_mode = mode
            logger.info(
                "Memory integrity validation enabled (mode=%s, key_source=%s)",
                mode, key_source
            )

        except Exception as e:
            logger.error("Failed to initialize integrity validation: %s", e)
            if not integrity_config.get("dev_mode", False):
                raise

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

        # Fix file permissions: restrict to owner only (0600)
        # This prevents other users on the system from reading API keys, user emails, etc.
        import os
        try:
            os.chmod(self.db_path, 0o600)
        except (OSError, FileNotFoundError):
            logger.warning("Could not set database file permissions to 0600 — may be a permissions issue")

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

    def ensure_conversation(self, conversation_id: str, created_at: str, user_id: str | None = None):
        """Create conversation record if it doesn't exist."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations (id, created_at, user_id) VALUES (?, ?, ?)",
                (conversation_id, created_at, user_id),
            )

    def store_message(self, msg: Message, cost_usd: float | None = None, latency_ms: float | None = None, ttft_ms: float | None = None, user_id: str | None = None):
        """
        Store a single message. Creates conversation if needed.

        If integrity validation is enabled, computes and stores HMAC signature.

        Args:
            msg: Message to store
            cost_usd: Optional cost in USD
            latency_ms: Optional latency in milliseconds
            ttft_ms: Optional time-to-first-token in milliseconds
            user_id: Optional user ID (used for integrity signature)
        """
        self.ensure_conversation(msg.conversation_id, msg.timestamp, user_id)

        # Compute HMAC signature if integrity is enabled
        message_hmac = None
        if self.integrity_validator and user_id:
            msg_dict = {
                "id": msg.id,
                "conversation_id": msg.conversation_id,
                "role": msg.role,
                "content": msg.content,
                "model": msg.model,
                "timestamp": msg.timestamp,
                "token_count": msg.token_count,
            }
            message_hmac = self.integrity_validator.sign_message(msg_dict, user_id)

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO messages
                   (id, conversation_id, role, content, model, timestamp, token_count, cost_usd, latency_ms, ttft_ms, message_hmac, integrity_version, tamper_detected)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (msg.id, msg.conversation_id, msg.role, msg.content,
                 msg.model, msg.timestamp, msg.token_count, cost_usd, latency_ms, ttft_ms,
                 message_hmac, 1, 0),
            )
        logger.debug("Stored message %s (role=%s, conv=%s)", msg.id, msg.role, msg.conversation_id)

    # ------------------------------------------------------------------
    # Capture-pipeline writers (v1.4)
    # ------------------------------------------------------------------
    # These take ``CapturedRequest`` / ``CapturedResponse`` envelopes from
    # ``beigebox.capture.CaptureFanout`` and write rows that include the new
    # nullable v1.4 columns (reasoning_text, tool_calls_json, finish_reason,
    # prompt/completion/reasoning_tokens, request_transforms_json,
    # normalize_errors_json, request_id, capture_outcome, error_kind,
    # error_message). Existing ``store_message`` is unchanged.

    def store_captured_request(self, req) -> list[str]:
        """Persist a captured request as one or more message rows.

        One row per non-empty user/assistant message in ``req.messages``.
        System messages are skipped — they're injected by BeigeBox itself
        and would pollute the conversation store on every turn (matching
        the old ``_log_messages`` behaviour).

        Returns the list of inserted message IDs (in order), so the caller
        can hand them to the vector store for embedding.
        """
        from uuid import uuid4
        ctx = req.ctx
        transforms_json = json.dumps(list(req.transforms)) if req.transforms else None
        errors_json = json.dumps(list(req.errors)) if req.errors else None
        request_id = ctx.request_id

        timestamp = ctx.started_at.isoformat() if ctx.started_at else datetime.now(timezone.utc).isoformat()
        self.ensure_conversation(ctx.conv_id, timestamp, ctx.user_id)

        inserted: list[str] = []
        with self._connect() as conn:
            for m in req.messages:
                if not isinstance(m, dict):
                    continue
                role = m.get("role", "")
                content = m.get("content", "")
                if not content or role == "system":
                    continue
                content_str = content if isinstance(content, str) else json.dumps(content)
                msg_id = uuid4().hex
                # HMAC: only on the signable subset, matching store_message().
                message_hmac = None
                if self.integrity_validator and ctx.user_id:
                    message_hmac = self.integrity_validator.sign_message(
                        {
                            "id": msg_id,
                            "conversation_id": ctx.conv_id,
                            "role": role,
                            "content": content_str,
                            "model": ctx.model,
                            "timestamp": timestamp,
                            "token_count": 0,
                        },
                        ctx.user_id,
                    )
                conn.execute(
                    """INSERT OR REPLACE INTO messages (
                        id, conversation_id, role, content, model, timestamp, token_count,
                        request_transforms_json, normalize_errors_json, request_id,
                        capture_outcome, message_hmac, integrity_version, tamper_detected
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        msg_id, ctx.conv_id, role, content_str,
                        ctx.model, timestamp, 0,
                        transforms_json, errors_json, request_id,
                        "ok", message_hmac, 1, 0,
                    ),
                )
                inserted.append(msg_id)
        return inserted

    def store_captured_response(self, resp) -> str:
        """Persist a captured response as one assistant row.

        Always writes a row, even when ``resp.outcome != "ok"`` — failures,
        aborts, and disconnects each get their full partial state stored
        with ``capture_outcome`` / ``error_kind`` / ``error_message`` set.

        Returns the inserted message ID.
        """
        from uuid import uuid4
        ctx = resp.ctx
        msg_id = uuid4().hex
        timestamp = (ctx.ended_at or datetime.now(timezone.utc)).isoformat()
        self.ensure_conversation(ctx.conv_id, timestamp, ctx.user_id)

        tool_calls_json = json.dumps(resp.tool_calls) if resp.tool_calls else None
        normalize_errors_json = json.dumps(list(resp.response_errors)) if resp.response_errors else None

        message_hmac = None
        if self.integrity_validator and ctx.user_id:
            message_hmac = self.integrity_validator.sign_message(
                {
                    "id": msg_id,
                    "conversation_id": ctx.conv_id,
                    "role": resp.role or "assistant",
                    "content": resp.content,
                    "model": ctx.model,
                    "timestamp": timestamp,
                    "token_count": resp.completion_tokens,
                },
                ctx.user_id,
            )

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO messages (
                    id, conversation_id, role, content, model, timestamp, token_count,
                    cost_usd, latency_ms, ttft_ms,
                    reasoning_text, tool_calls_json, finish_reason,
                    prompt_tokens, completion_tokens, reasoning_tokens,
                    normalize_errors_json, request_id,
                    capture_outcome, error_kind, error_message,
                    message_hmac, integrity_version, tamper_detected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg_id, ctx.conv_id, resp.role or "assistant", resp.content,
                    ctx.model, timestamp, resp.completion_tokens,
                    resp.cost_usd, ctx.latency_ms, ctx.ttft_ms,
                    resp.reasoning, tool_calls_json, resp.finish_reason,
                    resp.prompt_tokens, resp.completion_tokens, resp.reasoning_tokens,
                    normalize_errors_json, ctx.request_id,
                    resp.outcome, resp.error_kind, resp.error_message,
                    message_hmac, 1, 0,
                ),
            )
        return msg_id

    def get_conversation(self, conversation_id: str, user_id: str | None = None) -> tuple[list[dict], dict]:
        """
        Retrieve all messages for a conversation in order.

        If integrity validation is enabled, verifies signatures on read.
        Returns messages and integrity status.

        Args:
            conversation_id: Which conversation to retrieve
            user_id: Optional user ID (required for signature verification)

        Returns:
            Tuple of (messages, integrity_status)
            - messages: List of message dicts
            - integrity_status: {
                "valid": bool,
                "tampered_messages": list[str],  # message IDs with invalid signatures
                "unsigned_messages": list[str],  # message IDs with no signature
              }
        """
        with self._connect() as conn:
            # Get conversation
            conv = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()

            # Get all messages
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp",
                (conversation_id,),
            ).fetchall()

        messages = [dict(r) for r in rows]
        integrity_status = {
            "valid": True,
            "tampered_messages": [],
            "unsigned_messages": [],
        }

        # Verify integrity if enabled
        if self.integrity_validator and user_id and messages:
            unsigned = []
            tampered = []

            for msg in messages:
                msg_sig = msg.get("message_hmac")

                if not msg_sig:
                    unsigned.append(msg["id"])
                    continue

                # Extract only signable fields for verification
                msg_for_verify = self._extract_signable_fields(msg)

                if not self.integrity_validator.verify_message(msg_for_verify, user_id, msg_sig):
                    tampered.append(msg["id"])
                    # Mark message as tampered in database
                    with self._connect() as conn:
                        conn.execute(
                            "UPDATE messages SET tamper_detected = 1 WHERE id = ?",
                            (msg["id"],)
                        )

            integrity_status["unsigned_messages"] = unsigned
            integrity_status["tampered_messages"] = tampered

            # Determine overall validity and handle based on mode
            if unsigned or tampered:
                integrity_status["valid"] = False

                for msg_id in tampered:
                    IntegrityAuditLog.log_violation(
                        conversation_id, msg_id, user_id,
                        "signature_mismatch", self.integrity_mode
                    )

                for msg_id in unsigned:
                    IntegrityAuditLog.log_violation(
                        conversation_id, msg_id, user_id,
                        "missing_signature", self.integrity_mode
                    )

                # Handle based on mode
                if self.integrity_mode == "strict":
                    raise ValueError(
                        f"Conversation {conversation_id} failed integrity check: "
                        f"{len(tampered)} tampered, {len(unsigned)} unsigned"
                    )
                # "log_only" and "quarantine" both return the messages
                # but mark them as suspect for higher-level handling

        return messages, integrity_status

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
        messages, _ = self.get_conversation(source_conv_id)
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
            messages, _ = self.get_conversation(conv["id"])
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

