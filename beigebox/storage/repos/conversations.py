"""ConversationRepo — entity repo for conversations + messages.

Owns the conversations and messages tables plus the v1.4 capture columns.
Persistence-only: dispatch (the wire/vector/sqlite fan-out) lives in
``beigebox.capture.CaptureFanout``.

Sits on top of BaseDB; callers inject the driver. Holds the DDL,
HMAC integrity validation, the legacy ``store_message`` writer, the new
``store_captured_request`` / ``store_captured_response`` capture writers,
and the read/aggregate methods (get_conversation, get_recent_conversations,
get_model_performance, fork_conversation, exports, get_stats).

Migrated out of SQLiteStore on 2026-05-01; see project memory
"BeigeBox v3 / beigebox-security" for the demolition path.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from beigebox.security.memory_integrity import (
    ConversationIntegrityValidator,
    IntegrityAuditLog,
)
from beigebox.security.key_management import KeyManager
from beigebox.storage.integrity_helpers import extract_signable_fields
from beigebox.storage.models import Message

if TYPE_CHECKING:
    from beigebox.storage.db.base import BaseDB

logger = logging.getLogger(__name__)


_DDL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    user_id TEXT DEFAULT NULL,
    integrity_checked_at TEXT DEFAULT NULL
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
    message_hmac TEXT DEFAULT NULL,
    integrity_version INTEGER DEFAULT 1,
    tamper_detected BOOLEAN DEFAULT 0,
    reasoning_text TEXT DEFAULT NULL,
    tool_calls_json TEXT DEFAULT NULL,
    finish_reason TEXT DEFAULT NULL,
    prompt_tokens INTEGER DEFAULT NULL,
    completion_tokens INTEGER DEFAULT NULL,
    reasoning_tokens INTEGER DEFAULT NULL,
    request_transforms_json TEXT DEFAULT NULL,
    normalize_errors_json TEXT DEFAULT NULL,
    request_id TEXT DEFAULT NULL,
    capture_outcome TEXT DEFAULT NULL,
    error_kind TEXT DEFAULT NULL,
    error_message TEXT DEFAULT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp
    ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_role
    ON messages(role);
"""

# ALTER statements for upgrading existing DBs that pre-date these columns.
# Idempotent: BaseDB.execute will surface a duplicate-column error which
# we swallow at the call site (matches the legacy MIGRATIONS pattern).
_MIGRATIONS = [
    "ALTER TABLE messages ADD COLUMN cost_usd REAL DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN latency_ms REAL DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN custom_field_1 TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN custom_field_2 TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN ttft_ms REAL DEFAULT NULL",
    "ALTER TABLE conversations ADD COLUMN user_id TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN message_hmac TEXT DEFAULT NULL",
    "ALTER TABLE messages ADD COLUMN integrity_version INTEGER DEFAULT 1",
    "ALTER TABLE messages ADD COLUMN tamper_detected BOOLEAN DEFAULT 0",
    "ALTER TABLE conversations ADD COLUMN integrity_checked_at TEXT DEFAULT NULL",
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


class ConversationRepo:
    """Per-entity repository for conversations + messages."""

    def __init__(self, db: "BaseDB", integrity_config: dict | None = None) -> None:
        self._db = db
        self.integrity_validator: ConversationIntegrityValidator | None = None
        self.integrity_mode: str = "log_only"
        self._init_integrity(integrity_config or {})

    # ── schema ────────────────────────────────────────────────────────────

    def create_tables(self) -> None:
        """Idempotent DDL for conversations + messages + indexes + ALTERs."""
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._db.execute(stmt)
        # Apply migrations (ALTER ADD COLUMN). Each is idempotent in spirit
        # but raises "duplicate column" on the second run — swallow that
        # specific error and surface anything else.
        for migration in _MIGRATIONS:
            try:
                self._db.execute(migration)
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "already exists" not in msg:
                    logger.warning("Migration skipped: %s", e)

    # ── integrity validator setup (moved from SQLiteStore.__init__) ───────

    def _init_integrity(self, integrity_config: dict) -> None:
        """Initialize integrity validation if enabled.

        ``integrity_config`` keys: enabled, mode, key_source, key_path,
        dev_mode. Default disabled when no config provided.
        """
        if not integrity_config or not integrity_config.get("enabled", False):
            logger.info("Memory integrity validation disabled")
            return

        try:
            mode = integrity_config.get("mode", "log_only")
            key_source = integrity_config.get("key_source", "env")
            key_path = integrity_config.get("key_path", "~/.beigebox/memory.key")
            dev_mode = integrity_config.get("dev_mode", False)

            if key_path.startswith("~"):
                key_path = str(Path(key_path).expanduser())

            key = KeyManager.load_key(
                key_source=key_source,
                key_path=key_path,
                dev_mode=dev_mode,
            )
            if key is None:
                logger.warning(
                    "Memory integrity key not available (dev_mode=%s)", dev_mode,
                )
                return

            self.integrity_validator = ConversationIntegrityValidator(key)
            self.integrity_mode = mode
            logger.info(
                "Memory integrity validation enabled (mode=%s, key_source=%s)",
                mode, key_source,
            )
        except Exception as e:
            logger.error("Failed to initialize integrity validation: %s", e)
            if not integrity_config.get("dev_mode", False):
                raise

    def _sign(self, msg: dict, user_id: str | None) -> str | None:
        """Compute the HMAC signature for a message, or None if disabled."""
        if not (self.integrity_validator and user_id):
            return None
        return self.integrity_validator.sign_message(
            extract_signable_fields(msg), user_id,
        )

    # ── write operations ──────────────────────────────────────────────────

    def ensure_conversation(
        self,
        conversation_id: str,
        created_at: str,
        user_id: str | None = None,
    ) -> None:
        """Create a conversation row if it doesn't exist (no-op otherwise)."""
        ph = self._db._placeholder()
        self._db.execute(
            f"INSERT OR IGNORE INTO conversations (id, created_at, user_id) "
            f"VALUES ({ph}, {ph}, {ph})",
            (conversation_id, created_at, user_id),
        )

    def store_message(
        self,
        msg: Message,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        ttft_ms: float | None = None,
        user_id: str | None = None,
    ) -> None:
        """Store a single message. Creates conversation if missing.

        If integrity is enabled and ``user_id`` is provided, computes and
        stores the HMAC signature. The signed field set is the contract
        in ``beigebox.storage.integrity_helpers.SIGNABLE_FIELDS``.
        """
        self.ensure_conversation(msg.conversation_id, msg.timestamp, user_id)
        message_hmac = self._sign(
            {
                "id": msg.id,
                "conversation_id": msg.conversation_id,
                "role": msg.role,
                "content": msg.content,
                "model": msg.model,
                "timestamp": msg.timestamp,
                "token_count": msg.token_count,
            },
            user_id,
        )
        ph = self._db._placeholder()
        self._db.execute(
            f"INSERT OR REPLACE INTO messages "
            f"(id, conversation_id, role, content, model, timestamp, "
            f"token_count, cost_usd, latency_ms, ttft_ms, "
            f"message_hmac, integrity_version, tamper_detected) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, "
            f"{ph}, {ph}, {ph}, {ph}, "
            f"{ph}, {ph}, {ph})",
            (
                msg.id, msg.conversation_id, msg.role, msg.content,
                msg.model, msg.timestamp, msg.token_count,
                cost_usd, latency_ms, ttft_ms,
                message_hmac, 1, 0,
            ),
        )

    # ── capture-pipeline writers (v1.4) ───────────────────────────────────

    def store_captured_request(self, req) -> list[str]:
        """Persist a captured request as one or more message rows.

        One row per non-empty user/assistant message in ``req.messages``;
        system messages are skipped. Returns the list of inserted IDs in
        order, so the caller (CaptureFanout) can hand them to the vector
        store for embedding.
        """
        ctx = req.ctx
        transforms_json = json.dumps(list(req.transforms)) if req.transforms else None
        errors_json = json.dumps(list(req.errors)) if req.errors else None
        timestamp = (
            ctx.started_at.isoformat() if ctx.started_at
            else datetime.now(timezone.utc).isoformat()
        )
        self.ensure_conversation(ctx.conv_id, timestamp, ctx.user_id)

        ph = self._db._placeholder()
        sql = (
            f"INSERT OR REPLACE INTO messages ("
            f"id, conversation_id, role, content, model, timestamp, token_count, "
            f"request_transforms_json, normalize_errors_json, request_id, "
            f"capture_outcome, message_hmac, integrity_version, tamper_detected"
            f") VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, "
            f"{ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        inserted: list[str] = []
        with self._db.transaction() as tx:
            for m in req.messages:
                if not isinstance(m, dict):
                    continue
                role = m.get("role", "")
                content = m.get("content", "")
                if not content or role == "system":
                    continue
                content_str = content if isinstance(content, str) else json.dumps(content)
                msg_id = uuid4().hex
                message_hmac = self._sign(
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
                tx.execute(sql, (
                    msg_id, ctx.conv_id, role, content_str,
                    ctx.model, timestamp, 0,
                    transforms_json, errors_json, ctx.request_id,
                    "ok", message_hmac, 1, 0,
                ))
                inserted.append(msg_id)
        return inserted

    def store_captured_response(self, resp) -> str:
        """Persist a captured response as one assistant row.

        Always writes a row, even when ``resp.outcome != "ok"`` —
        failures, aborts, and disconnects each get their full partial
        state stored with ``capture_outcome``/``error_kind``/``error_message``
        set.
        """
        ctx = resp.ctx
        msg_id = uuid4().hex
        timestamp = (ctx.ended_at or datetime.now(timezone.utc)).isoformat()
        self.ensure_conversation(ctx.conv_id, timestamp, ctx.user_id)

        tool_calls_json = json.dumps(resp.tool_calls) if resp.tool_calls else None
        normalize_errors_json = (
            json.dumps(list(resp.response_errors)) if resp.response_errors else None
        )
        message_hmac = self._sign(
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
        ph = self._db._placeholder()
        self._db.execute(
            f"INSERT OR REPLACE INTO messages ("
            f"id, conversation_id, role, content, model, timestamp, token_count, "
            f"cost_usd, latency_ms, ttft_ms, "
            f"reasoning_text, tool_calls_json, finish_reason, "
            f"prompt_tokens, completion_tokens, reasoning_tokens, "
            f"normalize_errors_json, request_id, "
            f"capture_outcome, error_kind, error_message, "
            f"message_hmac, integrity_version, tamper_detected"
            f") VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, "
            f"{ph}, {ph}, {ph}, "
            f"{ph}, {ph}, {ph}, "
            f"{ph}, {ph}, {ph}, "
            f"{ph}, {ph}, "
            f"{ph}, {ph}, {ph}, "
            f"{ph}, {ph}, {ph})",
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

    # ── read operations ───────────────────────────────────────────────────

    def get_conversation(
        self,
        conversation_id: str,
        user_id: str | None = None,
    ) -> tuple[list[dict], dict]:
        """Retrieve all messages for a conversation in order, with HMAC verification.

        Returns ``(messages, integrity_status)`` where status is::

            {
              "valid": bool,
              "tampered_messages": list[str],   # message IDs with bad signatures
              "unsigned_messages": list[str],   # message IDs with no signature
            }

        When ``self.integrity_mode == "strict"`` and any message fails
        verification, raises ``ValueError``.
        """
        ph = self._db._placeholder()
        rows = self._db.fetchall(
            f"SELECT * FROM messages WHERE conversation_id = {ph} ORDER BY timestamp",
            (conversation_id,),
        )
        messages = [dict(r) for r in rows]
        integrity_status: dict = {
            "valid": True,
            "tampered_messages": [],
            "unsigned_messages": [],
        }

        if self.integrity_validator and user_id and messages:
            unsigned: list[str] = []
            tampered: list[str] = []
            for msg in messages:
                msg_sig = msg.get("message_hmac")
                if not msg_sig:
                    unsigned.append(msg["id"])
                    continue
                msg_for_verify = extract_signable_fields(msg)
                if not self.integrity_validator.verify_message(
                    msg_for_verify, user_id, msg_sig,
                ):
                    tampered.append(msg["id"])
                    self._db.execute(
                        f"UPDATE messages SET tamper_detected = 1 WHERE id = {ph}",
                        (msg["id"],),
                    )

            integrity_status["unsigned_messages"] = unsigned
            integrity_status["tampered_messages"] = tampered

            if unsigned or tampered:
                integrity_status["valid"] = False
                for mid in tampered:
                    IntegrityAuditLog.log_violation(
                        conversation_id, mid, user_id,
                        "signature_mismatch", self.integrity_mode,
                    )
                for mid in unsigned:
                    IntegrityAuditLog.log_violation(
                        conversation_id, mid, user_id,
                        "missing_signature", self.integrity_mode,
                    )
                if self.integrity_mode == "strict":
                    raise ValueError(
                        f"Conversation {conversation_id} failed integrity check: "
                        f"{len(tampered)} tampered, {len(unsigned)} unsigned"
                    )

        return messages, integrity_status

    def get_recent_conversations(self, limit: int = 20) -> list[dict]:
        """Most recent conversations, each annotated with last_message + count."""
        ph = self._db._placeholder()
        return self._db.fetchall(
            f"""SELECT c.id, c.created_at,
                       (SELECT content FROM messages m
                        WHERE m.conversation_id = c.id
                        ORDER BY m.timestamp DESC LIMIT 1) as last_message,
                       (SELECT COUNT(*) FROM messages m
                        WHERE m.conversation_id = c.id) as message_count
                FROM conversations c
                ORDER BY c.created_at DESC
                LIMIT {ph}""",
            (limit,),
        )

    def get_model_performance(
        self,
        days: int = 30,
        since: str | None = None,
    ) -> dict:
        """Per-model latency/throughput stats for the lookback window.

        Args:
            days:  lookback window (ignored when ``since`` is set)
            since: ISO timestamp string — only include data after this point
        """
        ph = self._db._placeholder()
        if since:
            ts_filter = since
            ts_clause = f"AND timestamp > {ph}"
        else:
            ts_filter = f"-{days} days"
            ts_clause = f"AND timestamp > datetime('now', {ph})"

        rows = self._db.fetchall(
            f"""SELECT model,
                       COUNT(*) as requests,
                       AVG(latency_ms) as avg_lat,
                       AVG(token_count) as avg_tok,
                       COALESCE(SUM(cost_usd), 0) as total_cost,
                       AVG(ttft_ms) as avg_ttft
                FROM messages
                WHERE role = 'assistant' AND latency_ms IS NOT NULL
                  {ts_clause}
                GROUP BY model
                ORDER BY requests DESC""",
            (ts_filter,),
        )
        detail_rows = self._db.fetchall(
            f"""SELECT model, latency_ms, ttft_ms, token_count FROM messages
                WHERE role = 'assistant' AND latency_ms IS NOT NULL
                  {ts_clause}
                ORDER BY model, latency_ms""",
            (ts_filter,),
        )
        perf_by_model: dict[str, list[tuple[float, float | None, int]]] = {}
        for r in detail_rows:
            perf_by_model.setdefault(r["model"], []).append(
                (r["latency_ms"], r["ttft_ms"], r["token_count"] or 0),
            )

        req_day_rows = self._db.fetchall(
            f"""SELECT DATE(timestamp) as day, COUNT(*) as requests
                FROM messages
                WHERE role = 'assistant' AND latency_ms IS NOT NULL
                  {ts_clause}
                GROUP BY DATE(timestamp)
                ORDER BY day""",
            (ts_filter,),
        )
        requests_by_day = {r["day"]: r["requests"] for r in req_day_rows}

        def _pct(vals: list[float], p: float) -> float:
            if not vals:
                return 0.0
            idx = min(int(len(vals) * p / 100), len(vals) - 1)
            return round(vals[idx], 1)

        def _avg_tps(rows: list[tuple[float, float | None, int]]) -> float:
            rates = []
            for lat, ttft, tok in rows:
                if tok <= 0:
                    continue
                gen_ms = (lat - ttft) if (ttft is not None and lat > ttft) else lat
                if gen_ms > 0:
                    rates.append(tok / (gen_ms / 1000.0))
            return round(sum(rates) / len(rates), 1) if rates else 0.0

        by_model: dict[str, dict] = {}
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

        return {
            "by_model": by_model,
            "days_queried": days,
            "requests_by_day": requests_by_day,
        }

    def fork_conversation(
        self,
        source_conv_id: str,
        new_conv_id: str,
        branch_at: int | None = None,
    ) -> int:
        """Fork conversation. Returns the number of messages copied."""
        messages, _ = self.get_conversation(source_conv_id)
        if branch_at is not None:
            messages = messages[: branch_at + 1]
        if not messages:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        self.ensure_conversation(new_conv_id, now)

        ph = self._db._placeholder()
        sql = (
            f"INSERT INTO messages "
            f"(id, conversation_id, role, content, model, "
            f"timestamp, token_count, cost_usd, latency_ms, ttft_ms) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, "
            f"{ph}, {ph}, {ph}, {ph}, {ph})"
        )
        with self._db.transaction() as tx:
            for msg in messages:
                tx.execute(sql, (
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
                ))
        logger.info(
            "Forked %d messages from %s → %s (branch_at=%s)",
            len(messages), source_conv_id, new_conv_id, branch_at,
        )
        return len(messages)

    # ── exports ───────────────────────────────────────────────────────────

    def export_all_json(self) -> list[dict]:
        """Export all conversations in OpenAI-compatible format."""
        conversations = self._db.fetchall(
            "SELECT id FROM conversations ORDER BY created_at",
        )
        result = []
        for conv in conversations:
            messages, _ = self.get_conversation(conv["id"])
            result.append({
                "conversation_id": conv["id"],
                "messages": [
                    {
                        "role": m["role"],
                        "content": m["content"],
                        "model": m["model"],
                        "timestamp": m["timestamp"],
                    }
                    for m in messages
                ],
            })
        return result

    def export_jsonl(self, model_filter: str | None = None) -> list[dict]:
        """Export as JSONL-style dicts (one per conversation)."""
        raw = self.export_all_json()
        result = []
        for conv in raw:
            msgs = [
                {"role": m["role"], "content": m["content"]}
                for m in conv["messages"]
                if m["role"] in ("user", "assistant")
                and (not model_filter or m.get("model") == model_filter)
            ]
            roles = {m["role"] for m in msgs}
            if "user" in roles and "assistant" in roles:
                result.append({"messages": msgs})
        return result

    def export_alpaca(self, model_filter: str | None = None) -> list[dict]:
        """Export as Alpaca-format instruction pairs."""
        raw = self.export_all_json()
        result = []
        for conv in raw:
            msgs = [
                m for m in conv["messages"]
                if m["role"] in ("user", "assistant")
                and (not model_filter or m.get("model") == model_filter)
            ]
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
        """Export as ShareGPT format."""
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

    # ── stats ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Counts + token totals + per-model cost."""
        conv_count = self._db.fetchone("SELECT COUNT(*) AS n FROM conversations")["n"]
        msg_count = self._db.fetchone("SELECT COUNT(*) AS n FROM messages")["n"]
        user_count = self._db.fetchone(
            "SELECT COUNT(*) AS n FROM messages WHERE role='user'",
        )["n"]
        asst_count = self._db.fetchone(
            "SELECT COUNT(*) AS n FROM messages WHERE role='assistant'",
        )["n"]

        total_tokens = self._db.fetchone(
            "SELECT COALESCE(SUM(token_count), 0) AS n FROM messages",
        )["n"]
        user_tokens = self._db.fetchone(
            "SELECT COALESCE(SUM(token_count), 0) AS n FROM messages WHERE role='user'",
        )["n"]
        asst_tokens = self._db.fetchone(
            "SELECT COALESCE(SUM(token_count), 0) AS n FROM messages WHERE role='assistant'",
        )["n"]

        model_rows = self._db.fetchall(
            """SELECT model,
                      COUNT(*) as messages,
                      COALESCE(SUM(token_count), 0) as tokens,
                      COALESCE(SUM(cost_usd), 0) as cost
               FROM messages
               WHERE model != ''
               GROUP BY model
               ORDER BY messages DESC"""
        )
        models = {
            row["model"]: {
                "messages": row["messages"],
                "tokens": row["tokens"],
                "cost_usd": row["cost"],
            }
            for row in model_rows
        }

        total_cost = self._db.fetchone(
            "SELECT COALESCE(SUM(cost_usd), 0) AS n FROM messages",
        )["n"]

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
