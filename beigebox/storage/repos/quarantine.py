"""
QuarantineRepo — entity repo for the quarantined_embeddings table.

Sits on top of BaseDB; callers inject the driver. Holds the schema, the
embedding-fingerprint hashing rule, and the four access patterns (log, search,
stats, purge) that the rest of the codebase needs.

Migrated out of SQLiteStore on 2026-05-01; see project memory
"BeigeBox v3 / beigebox-security" for the demolition path.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beigebox.storage.db.base import BaseDB

logger = logging.getLogger(__name__)


_DDL = """
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
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class QuarantineRepo:
    """Per-entity repository for quarantined_embeddings.

    Inject a BaseDB instance; the repo owns the schema and access patterns
    for this table. It does NOT own the connection lifecycle — callers
    create and close the db.
    """

    def __init__(self, db: "BaseDB") -> None:
        self._db = db

    def create_tables(self) -> None:
        """Idempotent DDL for quarantined_embeddings (+ indexes)."""
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._db.execute(stmt)

    # ── write operations ───────────────────────────────────────────────────

    def log(
        self,
        document_id: str,
        embedding: list | None,
        confidence: float,
        reason: str,
        method: str = "magnitude",
    ) -> int | None:
        """Log a quarantined embedding.

        Args:
            document_id: ID of the message/document that was quarantined
            embedding: Optional embedding vector (for hash fingerprinting)
            confidence: Detection confidence in [0.0, 1.0]
            reason: Human-readable explanation
            method: Detection method (e.g., 'magnitude', 'zscore', 'centroid')

        Returns:
            ID of the inserted record, or None if BaseDB.insert can't return one.
        """
        embedding_hash = None
        if embedding:
            emb_bytes = str(embedding[:10]).encode()  # first 10 dims only
            embedding_hash = hashlib.sha256(emb_bytes).hexdigest()[:16]

        return self._db.insert(
            "quarantined_embeddings",
            {
                "document_id": document_id,
                "embedding_hash": embedding_hash,
                "confidence": confidence,
                "reason": reason,
                "detector_method": method,
            },
        )

    # ── read operations ────────────────────────────────────────────────────

    def get_by_id(self, record_id: int) -> dict | None:
        """Return one quarantine record by its primary key, or None."""
        ph = self._db._placeholder()
        return self._db.fetchone(
            f"SELECT * FROM quarantined_embeddings WHERE id = {ph}",
            (record_id,),
        )

    def search(self, filters: str = "all", limit: int = 100) -> list[dict]:
        """Search quarantine table.

        Args:
            filters: 'recent' (last 24h), 'suspicious' (confidence > 0.8), or 'all'
            limit:   Max results to return
        """
        ph = self._db._placeholder()
        query = "SELECT * FROM quarantined_embeddings"
        params: list = []

        if filters == "recent":
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            query += f" WHERE timestamp >= {ph}"
            params.append(cutoff)
        elif filters == "suspicious":
            query += " WHERE confidence > 0.8"

        query += f" ORDER BY timestamp DESC LIMIT {ph}"
        params.append(limit)

        return self._db.fetchall(query, tuple(params))

    def get_stats(self) -> dict:
        """Return aggregate stats matching the legacy SQLiteStore.get_quarantine_stats() shape.

        {
            "total": int,
            "high_confidence": int,    # > 0.8
            "medium_confidence": int,  # 0.5..0.8
            "avg_confidence": float,
            "confidence_p50": float,
            "confidence_p95": float,
            "reasons": {reason: count},
            "methods": {method: count},
            "last_24h": int,
        }
        """
        ph = self._db._placeholder()
        db = self._db

        total = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM quarantined_embeddings"
        )["cnt"]

        high = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM quarantined_embeddings WHERE confidence > 0.8"
        )["cnt"]

        medium = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM quarantined_embeddings WHERE confidence BETWEEN 0.5 AND 0.8"
        )["cnt"]

        avg_row = db.fetchone(
            "SELECT AVG(confidence) AS avg FROM quarantined_embeddings"
        )
        avg_conf = avg_row["avg"] if avg_row else None

        p50_row = db.fetchone(
            "SELECT confidence FROM quarantined_embeddings "
            "ORDER BY confidence LIMIT 1 OFFSET ("
            "  SELECT COUNT(*)/2 FROM quarantined_embeddings)"
        )
        p50_val = p50_row["confidence"] if p50_row else 0.0

        p95_row = db.fetchone(
            "SELECT confidence FROM quarantined_embeddings "
            "ORDER BY confidence DESC LIMIT 1 OFFSET ("
            "  SELECT MAX(0, CAST(COUNT(*) * 0.05 AS INTEGER)) FROM quarantined_embeddings)"
        )
        p95_val = p95_row["confidence"] if p95_row else 0.0

        reason_rows = db.fetchall(
            "SELECT reason, COUNT(*) AS cnt FROM quarantined_embeddings "
            "GROUP BY reason ORDER BY cnt DESC LIMIT 5"
        )
        reasons = {row["reason"]: row["cnt"] for row in reason_rows}

        method_rows = db.fetchall(
            "SELECT detector_method, COUNT(*) AS cnt FROM quarantined_embeddings "
            "GROUP BY detector_method ORDER BY cnt DESC"
        )
        methods = {row["detector_method"]: row["cnt"] for row in method_rows}

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        last_24h = db.fetchone(
            f"SELECT COUNT(*) AS cnt FROM quarantined_embeddings WHERE timestamp >= {ph}",
            (cutoff,),
        )["cnt"]

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

    # ── maintenance ────────────────────────────────────────────────────────

    def purge(self, days: int = 30, dry_run: bool = False) -> int:
        """Delete records older than `days`. Returns the deleted (or would-be) count.

        A negative `days` value purges everything (the cutoff lands in the future).
        """
        ph = self._db._placeholder()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        count = self._db.fetchone(
            f"SELECT COUNT(*) AS cnt FROM quarantined_embeddings WHERE timestamp < {ph}",
            (cutoff,),
        )["cnt"]

        if not dry_run:
            self._db.execute(
                f"DELETE FROM quarantined_embeddings WHERE timestamp < {ph}",
                (cutoff,),
            )

        return count
