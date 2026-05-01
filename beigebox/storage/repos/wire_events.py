"""
WireEventRepo — entity repo for the wire_events table.

Sits on top of BaseDB. Owns the schema, the JSON serialization for the
meta field, and the read/write access patterns used by the structured
wire-tap (web UI cross-linking by conv_id / run_id).

Migrated out of SQLiteStore on 2026-05-01; SqliteWireSink writes via this
repo; the read endpoint at /api/v1/tap also queries through it.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beigebox.storage.db.base import BaseDB

logger = logging.getLogger(__name__)


_DDL = """
CREATE TABLE IF NOT EXISTS wire_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    source      TEXT NOT NULL,
    conv_id     TEXT,
    run_id      TEXT,
    turn_id     TEXT,
    tool_id     TEXT,
    model       TEXT,
    role        TEXT,
    content     TEXT,
    meta        TEXT,
    misc1       TEXT,
    misc2       TEXT
);
CREATE INDEX IF NOT EXISTS idx_wire_events_conv ON wire_events(conv_id);
CREATE INDEX IF NOT EXISTS idx_wire_events_run  ON wire_events(run_id);
CREATE INDEX IF NOT EXISTS idx_wire_events_type ON wire_events(event_type);
CREATE INDEX IF NOT EXISTS idx_wire_events_ts   ON wire_events(ts);
"""

_CONTENT_TRUNCATION_LIMIT = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(content: str) -> str:
    if len(content) <= _CONTENT_TRUNCATION_LIMIT:
        return content
    return (
        content[:1000]
        + f"\n\n[...{len(content) - _CONTENT_TRUNCATION_LIMIT} chars truncated...]\n\n"
        + content[-1000:]
    )


class WireEventRepo:
    """Per-entity repository for wire_events."""

    def __init__(self, db: "BaseDB") -> None:
        self._db = db

    def create_tables(self) -> None:
        """Idempotent DDL for wire_events (+ indexes)."""
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._db.execute(stmt)

    # ── write ──────────────────────────────────────────────────────────────

    def log(
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
        """Insert one wire event. Errors are logged but not raised — the
        wire log must never break the proxy hot path."""
        ts = _now_iso()
        content = _truncate(content)
        meta_str = json.dumps(meta, ensure_ascii=False) if meta else None
        ph = self._db._placeholder()
        try:
            self._db.execute(
                f"INSERT INTO wire_events "
                f"(ts, event_type, source, conv_id, run_id, turn_id, tool_id, "
                f" model, role, content, meta, misc1, misc2) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, "
                f"        {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                (ts, event_type, source, conv_id, run_id, turn_id, tool_id,
                 model, role, content, meta_str, misc1, misc2),
            )
        except Exception as e:
            logger.warning("WireEventRepo.log failed (%s/%s): %s", source, event_type, e)

    # ── read ───────────────────────────────────────────────────────────────

    def query(
        self,
        n: int = 100,
        event_type: str | None = None,
        source: str | None = None,
        conv_id: str | None = None,
        run_id: str | None = None,
        role: str | None = None,
    ) -> list[dict]:
        """Return newest-first wire events with optional filters.

        meta is JSON-decoded into a dict in each row.
        """
        ph = self._db._placeholder()
        clauses: list[str] = []
        params: list = []
        if event_type:
            clauses.append(f"event_type = {ph}")
            params.append(event_type)
        if source:
            clauses.append(f"source = {ph}")
            params.append(source)
        if conv_id:
            clauses.append(f"conv_id = {ph}")
            params.append(conv_id)
        if run_id:
            clauses.append(f"run_id = {ph}")
            params.append(run_id)
        if role:
            clauses.append(f"role = {ph}")
            params.append(role)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(n)

        rows = self._db.fetchall(
            f"SELECT * FROM wire_events {where} ORDER BY id DESC LIMIT {ph}",
            tuple(params),
        )
        events = []
        for row in rows:
            if row.get("meta"):
                try:
                    row["meta"] = json.loads(row["meta"])
                except Exception:
                    pass
            events.append(row)
        return events
