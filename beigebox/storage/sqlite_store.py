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

CREATE TABLE IF NOT EXISTS eval_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_name  TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    input       TEXT NOT NULL,
    output      TEXT,
    passed      INTEGER NOT NULL,
    score       REAL,
    scorer      TEXT,
    model       TEXT,
    latency_ms  REAL,
    reason      TEXT,
    error       TEXT,
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    misc1       TEXT,
    misc2       TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_results_suite
    ON eval_results(suite_name);
CREATE INDEX IF NOT EXISTS idx_eval_results_run
    ON eval_results(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_ts
    ON eval_results(ts);

CREATE TABLE IF NOT EXISTS model_specs (
    id INTEGER PRIMARY KEY,
    model_name TEXT UNIQUE NOT NULL,
    backend TEXT NOT NULL,
    vram_mb INTEGER,
    ram_mb INTEGER,
    params_billions REAL,
    discovered_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    discovery_method TEXT,
    last_seen_loaded TEXT,
    notes TEXT,
    misc1 TEXT,
    misc2 TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_specs_name
    ON model_specs(model_name);

CREATE TABLE IF NOT EXISTS discovery_scorecards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    opportunity_id  TEXT NOT NULL,
    variant_name    TEXT NOT NULL,
    accuracy        REAL,
    efficiency      REAL,
    clarity         REAL,
    hallucination   REAL,
    safety          REAL,
    overall_score   REAL,
    oracle_passed   INTEGER,
    weight_profile  TEXT,
    ts              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    misc1           TEXT,
    misc2           TEXT
);
CREATE INDEX IF NOT EXISTS idx_discovery_scorecards_run
    ON discovery_scorecards(run_id);
CREATE INDEX IF NOT EXISTS idx_discovery_scorecards_opportunity
    ON discovery_scorecards(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_discovery_scorecards_ts
    ON discovery_scorecards(ts);

CREATE TABLE IF NOT EXISTS orchestration_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    description     TEXT,
    config          TEXT NOT NULL,
    worker_type     TEXT,
    max_rounds      INTEGER DEFAULT 8,
    max_iterations  INTEGER DEFAULT 10,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    misc1           TEXT,
    misc2           TEXT
);
CREATE INDEX IF NOT EXISTS idx_orchestration_profiles_name
    ON orchestration_profiles(name);
CREATE INDEX IF NOT EXISTS idx_orchestration_profiles_enabled
    ON orchestration_profiles(enabled);

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

CREATE TABLE IF NOT EXISTS operator_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,          -- FK → operator_runs.id
    turn_n      INTEGER NOT NULL,       -- 0-based turn index within the run
    input_sha   TEXT,                   -- git short SHA — content-addressable replay key
    input_chars INTEGER,                -- pre-prune character count
    store_path  TEXT,                   -- path to context_store repo for git show
    model       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    misc1       TEXT,
    misc2       TEXT
);
CREATE INDEX IF NOT EXISTS idx_operator_turns_run ON operator_turns(run_id);
CREATE INDEX IF NOT EXISTS idx_operator_turns_sha ON operator_turns(input_sha);

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
    # v0.8 — trajectory evaluation scores for operator runs
    "ALTER TABLE operator_runs ADD COLUMN score_json TEXT DEFAULT NULL",
    # v0.9 — structured wire events table (tap redesign)
    # CREATE TABLE is in CREATE_TABLES (IF NOT EXISTS), migrations only needed for
    # existing DBs that don't have the table yet — handled by _init_db CREATE_TABLES.
    # Index migrations are also safe (CREATE INDEX IF NOT EXISTS in CREATE_TABLES).
    # v1.0 — web auth: user tracking
    "ALTER TABLE conversations ADD COLUMN user_id TEXT DEFAULT NULL",
    # v1.1 — per-turn context archive index (operator_turns table is in CREATE_TABLES)
    "SELECT 1",  # no-op placeholder; table created via CREATE TABLE IF NOT EXISTS above
    # v1.2 — memory integrity validation columns
    "ALTER TABLE messages ADD COLUMN message_hmac TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN integrity_version INTEGER DEFAULT 1",
    "ALTER TABLE messages ADD COLUMN tamper_detected BOOLEAN DEFAULT 0",
    "ALTER TABLE conversations ADD COLUMN integrity_checked_at TEXT DEFAULT NULL",
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

    # ------------------------------------------------------------------
    # User management (web auth)
    # ------------------------------------------------------------------

    def upsert_user(self, provider: str, sub: str, email: str, name: str, picture: str) -> str:
        """Insert or update a user row; return the stable user_id UUID."""
        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE provider=? AND sub=?",
                (provider, sub),
            ).fetchone()
            if row:
                user_id = row["id"]
                conn.execute(
                    "UPDATE users SET email=?, name=?, picture=?, last_seen=? WHERE id=?",
                    (email, name, picture, now, user_id),
                )
            else:
                user_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO users (id, provider, sub, email, name, picture, created_at, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, provider, sub, email, name, picture, now, now),
                )
        return user_id

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def create_api_key(self, user_id: str, name: str = "default") -> tuple[str, str]:
        """Create a new API key for a user. Returns (key_id, plain_key)."""
        import uuid
        import secrets
        from datetime import datetime, timezone
        try:
            import bcrypt
        except ImportError:
            raise ImportError("bcrypt is required for API key hashing. Install: pip install bcrypt")

        key_id = str(uuid.uuid4())
        plain_key = secrets.token_urlsafe(32)
        # Bcrypt hash with cost factor 12 (industry standard, ~100ms per hash)
        key_hash = bcrypt.hashpw(plain_key.encode(), bcrypt.gensalt(rounds=12)).decode()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO api_keys (id, user_id, key_hash, name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_id, user_id, key_hash, name, now),
            )
        return key_id, plain_key

    def verify_api_key(self, plain_key: str) -> str | None:
        """Verify an API key. Return user_id if valid, None otherwise.

        Uses constant-time bcrypt verification to prevent timing attacks.
        """
        try:
            import bcrypt
        except ImportError:
            raise ImportError("bcrypt is required for API key verification. Install: pip install bcrypt")

        with self._connect() as conn:
            # Fetch all active, non-expired keys for this user
            # Note: We can't use WHERE key_hash=plain_key with bcrypt, so we fetch all and check
            rows = conn.execute(
                "SELECT user_id, key_hash FROM api_keys WHERE active=1 "
                "AND (expires_at IS NULL OR expires_at > datetime('now'))",
            ).fetchall()

            for row in rows:
                stored_hash = row[1]
                try:
                    # Constant-time comparison using bcrypt
                    if bcrypt.checkpw(plain_key.encode(), stored_hash.encode()):
                        user_id = row[0]
                        # Update last_used timestamp (after finding match, for performance)
                        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        conn.execute(
                            "UPDATE api_keys SET last_used=? WHERE key_hash=?",
                            (now, stored_hash),
                        )
                        return user_id
                except ValueError:
                    # Invalid bcrypt hash (shouldn't happen with valid DB)
                    continue

            # Constant-time check: always do at least one bcrypt operation
            # even if no keys found, to prevent timing attacks
            try:
                bcrypt.checkpw(
                    plain_key.encode(),
                    bcrypt.gensalt(rounds=4).encode()  # dummy hash for constant timing
                )
            except ValueError:
                pass

            return None

    def get_api_keys(self, user_id: str) -> list[dict]:
        """List all API keys for a user (excluding plaintext key)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at, last_used, active FROM api_keys "
                "WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def revoke_api_key(self, key_id: str, user_id: str) -> bool:
        """Revoke an API key. Returns True if successful."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET active=0 WHERE id=? AND user_id=?",
                (key_id, user_id),
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # RAG Poisoning Quarantine Management
    # ------------------------------------------------------------------

    def log_quarantine(
        self,
        document_id: str,
        embedding: list | None,
        confidence: float,
        reason: str,
        method: str = "magnitude",
    ) -> int:
        """
        Log a quarantined embedding to the database.

        Args:
            document_id: ID of the message/document that was quarantined
            embedding: Optional embedding vector (for hash computation)
            confidence: Detection confidence [0.0, 1.0]
            reason: Human-readable explanation
            method: Detection method (e.g., 'magnitude', 'zscore', 'centroid')

        Returns:
            ID of the inserted record
        """
        import hashlib

        embedding_hash = None
        if embedding:
            # Compute hash of embedding for fingerprinting
            emb_bytes = str(embedding[:10]).encode()  # first 10 dims only
            embedding_hash = hashlib.sha256(emb_bytes).hexdigest()[:16]

        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO quarantined_embeddings
                   (document_id, embedding_hash, confidence, reason, detector_method)
                   VALUES (?, ?, ?, ?, ?)""",
                (document_id, embedding_hash, confidence, reason, method),
            )
            return cursor.lastrowid

    def search_quarantined(
        self,
        filters: str = "all",
        limit: int = 100,
    ) -> list[dict]:
        """
        Search quarantine table with optional filtering.

        Args:
            filters: 'recent' (24h), 'suspicious' (confidence > 0.8), or 'all'
            limit: Max results to return

        Returns:
            List of quarantine records
        """
        from datetime import datetime, timedelta, timezone

        query = "SELECT * FROM quarantined_embeddings"
        params: list = []

        if filters == "recent":
            # Last 24 hours
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            query += " WHERE timestamp >= ?"
            params.append(cutoff)
        elif filters == "suspicious":
            # High confidence (> 0.8)
            query += " WHERE confidence > 0.8"

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_quarantine_stats(self) -> dict:
        """
        Get aggregate statistics about quarantined embeddings.

        Returns:
            {
                "total": int,
                "high_confidence": int,  # > 0.8
                "medium_confidence": int,  # 0.5-0.8
                "avg_confidence": float,
                "confidence_p50": float,
                "confidence_p95": float,
                "reasons": {reason: count},
                "methods": {method: count},
                "last_24h": int,
            }
        """
        from datetime import datetime, timedelta, timezone

        with self._connect() as conn:
            # Overall stats
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM quarantined_embeddings"
            ).fetchone()["cnt"]

            high = conn.execute(
                "SELECT COUNT(*) as cnt FROM quarantined_embeddings WHERE confidence > 0.8"
            ).fetchone()["cnt"]

            medium = conn.execute(
                "SELECT COUNT(*) as cnt FROM quarantined_embeddings WHERE confidence BETWEEN 0.5 AND 0.8"
            ).fetchone()["cnt"]

            avg_conf = conn.execute(
                "SELECT AVG(confidence) as avg FROM quarantined_embeddings"
            ).fetchone()["avg"]

            p50 = conn.execute(
                "SELECT confidence FROM quarantined_embeddings ORDER BY confidence LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM quarantined_embeddings)"
            ).fetchone()
            p50_val = p50["confidence"] if p50 else 0.0

            # Percentile approximation (order by, skip)
            p95_row = conn.execute(
                """SELECT confidence FROM quarantined_embeddings
                   ORDER BY confidence DESC LIMIT 1 OFFSET
                   (SELECT MAX(0, CAST(COUNT(*) * 0.05 AS INTEGER)) FROM quarantined_embeddings)"""
            ).fetchone()
            p95_val = p95_row["confidence"] if p95_row else 0.0

            # Reason breakdown
            reason_rows = conn.execute(
                """SELECT reason, COUNT(*) as cnt FROM quarantined_embeddings
                   GROUP BY reason ORDER BY cnt DESC LIMIT 5"""
            ).fetchall()
            reasons = {row["reason"]: row["cnt"] for row in reason_rows}

            # Method breakdown
            method_rows = conn.execute(
                """SELECT detector_method, COUNT(*) as cnt FROM quarantined_embeddings
                   GROUP BY detector_method ORDER BY cnt DESC"""
            ).fetchall()
            methods = {row["detector_method"]: row["cnt"] for row in method_rows}

            # Last 24h
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            last_24h = conn.execute(
                "SELECT COUNT(*) as cnt FROM quarantined_embeddings WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()["cnt"]

        return {
            "total": total,
            "high_confidence": high,
            "medium_confidence": medium,
            "avg_confidence": avg_conf or 0.0,
            "confidence_p50": p50_val,
            "confidence_p95": p95_val,
            "reasons": reasons,
            "methods": methods,
            "last_24h": last_24h,
        }

    def purge_quarantine(
        self,
        days: int = 30,
        dry_run: bool = False,
    ) -> int:
        """
        Delete quarantine records older than N days.

        Args:
            days: Age threshold (delete records older than this)
            dry_run: If True, count only (don't delete)

        Returns:
            Number of records deleted (or would be deleted in dry-run)
        """
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        with self._connect() as conn:
            # Count
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM quarantined_embeddings WHERE timestamp < ?",
                (cutoff,),
            ).fetchone()["cnt"]

            if not dry_run:
                conn.execute(
                    "DELETE FROM quarantined_embeddings WHERE timestamp < ?",
                    (cutoff,),
                )

        return count

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

    # ─ Operator turns (context archive index) ────────────────────────────────

    def store_operator_turn(
        self,
        run_id: str,
        turn_n: int,
        input_sha: str | None,
        input_chars: int,
        store_path: str | None,
        model: str = "",
    ) -> None:
        """
        Record one archived turn in the operator_turns table.

        input_sha + store_path together form the replay key:
            git -C <store_path> show <input_sha>
        """
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO operator_turns
                   (run_id, turn_n, input_sha, input_chars, store_path, model)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, turn_n, input_sha, input_chars, store_path, model),
            )
        logger.debug("Stored operator turn %s/%d sha=%s", run_id, turn_n, input_sha or "none")

    def list_operator_turns(self, run_id: str) -> list[dict]:
        """Return all archived turns for a run, ordered by turn_n."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT turn_n, input_sha, input_chars, store_path, model, created_at
                   FROM operator_turns
                   WHERE run_id = ?
                   ORDER BY turn_n""",
                (run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

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

    # ─ Eval results ───────────────────────────────────────────────────────────

    def store_eval_result(self, suite_name: str, result) -> None:
        """Persist one EvalResult row. result is an EvalResult dataclass."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO eval_results
                    (suite_name, case_id, run_id, input, output, passed, score,
                     scorer, model, latency_ms, reason, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suite_name,
                    result.case_id,
                    result.run_id,
                    result.input,
                    result.output or "",
                    1 if result.passed else 0,
                    result.score,
                    result.scorer,
                    result.model or "",
                    result.latency_ms,
                    result.reason or "",
                    result.error or "",
                ),
            )

    def get_eval_results(
        self,
        suite_name: str | None = None,
        run_id: str | None = None,
        n: int = 100,
    ) -> list[dict]:
        """Fetch recent eval results, optionally filtered by suite or run."""
        clauses: list[str] = []
        params: list = []
        if suite_name:
            clauses.append("suite_name = ?")
            params.append(suite_name)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(n)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM eval_results {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def store_model_spec(
        self,
        model_name: str,
        backend: str,
        vram_mb: int | None = None,
        ram_mb: int | None = None,
        params_billions: float | None = None,
        discovery_method: str = "ollama_ps",
        notes: str | None = None,
    ) -> None:
        """Upsert a model spec row. Called passively from health/metrics checks."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO model_specs
                   (model_name, backend, vram_mb, ram_mb, params_billions,
                    discovered_at, discovery_method, last_seen_loaded, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(model_name) DO UPDATE SET
                     backend           = excluded.backend,
                     vram_mb           = COALESCE(excluded.vram_mb, vram_mb),
                     ram_mb            = COALESCE(excluded.ram_mb, ram_mb),
                     params_billions   = COALESCE(excluded.params_billions, params_billions),
                     discovery_method  = excluded.discovery_method,
                     last_seen_loaded  = excluded.last_seen_loaded,
                     notes             = COALESCE(excluded.notes, notes)
                """,
                (model_name, backend, vram_mb, ram_mb, params_billions,
                 now, discovery_method, now, notes),
            )

    def get_model_specs(self) -> list[dict]:
        """Return all stored model specs, most recently seen first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM model_specs ORDER BY last_seen_loaded DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ─ Discovery scorecards (context optimization) ────────────────────────────────

    def store_discovery_scorecard(
        self,
        run_id: str,
        opportunity_id: str,
        variant_name: str,
        accuracy: float,
        efficiency: float,
        clarity: float,
        hallucination: float,
        safety: float,
        overall_score: float,
        oracle_passed: bool,
        weight_profile: str | None = None,
    ) -> None:
        """Store a single discovery scorecard."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO discovery_scorecards
                   (run_id, opportunity_id, variant_name, accuracy, efficiency,
                    clarity, hallucination, safety, overall_score, oracle_passed,
                    weight_profile)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    opportunity_id,
                    variant_name,
                    accuracy,
                    efficiency,
                    clarity,
                    hallucination,
                    safety,
                    overall_score,
                    1 if oracle_passed else 0,
                    weight_profile,
                ),
            )
        logger.debug(
            f"Stored discovery scorecard: {run_id}/{opportunity_id}/{variant_name} "
            f"(score={overall_score:.3f})"
        )

    def get_discovery_scorecards(
        self,
        opportunity_id: str | None = None,
        run_id: str | None = None,
        n: int = 100,
    ) -> list[dict]:
        """Fetch recent discovery scorecards, optionally filtered."""
        clauses: list[str] = []
        params: list = []
        if opportunity_id:
            clauses.append("opportunity_id = ?")
            params.append(opportunity_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(n)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM discovery_scorecards {where} "
                f"ORDER BY ts DESC LIMIT ?",
                params,
            ).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            # Convert oracle_passed from int to bool
            r["oracle_passed"] = bool(r.get("oracle_passed", 0))
            results.append(r)
        return results

    # ─ Orchestration profiles ────────────────────────────────────────────────────

    def create_orchestration_profile(
        self,
        name: str,
        config: Dict[str, Any],
        description: str | None = None,
        worker_type: str | None = None,
        max_rounds: int = 8,
        max_iterations: int = 10,
    ) -> Dict[str, Any]:
        """Create a new orchestration profile."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO orchestration_profiles
                   (name, description, config, worker_type, max_rounds, max_iterations)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, description, json.dumps(config), worker_type, max_rounds, max_iterations),
            )
            row = conn.execute(
                "SELECT * FROM orchestration_profiles WHERE name = ?",
                (name,),
            ).fetchone()
        return dict(row) if row else {}

    def get_orchestration_profile(self, name: str) -> Dict[str, Any] | None:
        """Fetch a single orchestration profile by name."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM orchestration_profiles WHERE name = ?",
                (name,),
            ).fetchone()

        if not row:
            return None

        profile = dict(row)
        profile["config"] = json.loads(profile.get("config", "{}"))
        profile["enabled"] = bool(profile.get("enabled", 1))
        return profile

    def list_orchestration_profiles(self, enabled_only: bool = False) -> list[Dict[str, Any]]:
        """List all orchestration profiles."""
        where = "WHERE enabled = 1" if enabled_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM orchestration_profiles {where} ORDER BY name"
            ).fetchall()

        profiles = []
        for row in rows:
            p = dict(row)
            p["config"] = json.loads(p.get("config", "{}"))
            p["enabled"] = bool(p.get("enabled", 1))
            profiles.append(p)
        return profiles

    def update_orchestration_profile(
        self,
        name: str,
        **updates,
    ) -> Dict[str, Any] | None:
        """Update an orchestration profile."""
        from datetime import datetime, timezone

        allowed_fields = {"description", "config", "worker_type", "max_rounds", "max_iterations", "enabled"}
        update_fields = {k: v for k, v in updates.items() if k in allowed_fields}

        if not update_fields:
            return self.get_orchestration_profile(name)

        # Convert config to JSON if present
        if "config" in update_fields and isinstance(update_fields["config"], dict):
            update_fields["config"] = json.dumps(update_fields["config"])

        # Add updated_at timestamp
        update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clause = ", ".join(f"{k} = ?" for k in update_fields.keys())
        params = list(update_fields.values()) + [name]

        with self._connect() as conn:
            conn.execute(
                f"UPDATE orchestration_profiles SET {set_clause} WHERE name = ?",
                params,
            )

        return self.get_orchestration_profile(name)

    def delete_orchestration_profile(self, name: str) -> bool:
        """Delete an orchestration profile."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM orchestration_profiles WHERE name = ?",
                (name,),
            )
            return cursor.rowcount > 0

