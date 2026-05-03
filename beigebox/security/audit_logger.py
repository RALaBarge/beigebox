"""
Audit Logging for Security Decisions

Assumption: Isolation will fail someday. Attackers WILL find bypasses.
This logger captures every security decision so we can:
  1. Detect bypasses retroactively
  2. Understand attack patterns
  3. Build better defenses

Every validation decision is logged with:
  - Timestamp
  - Tool/function
  - Input parameters
  - Decision (allow/deny)
  - Reason
  - Fingerprint (hash of input)
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AuditLogEntry:
    """Single security audit log entry."""
    timestamp: str                  # ISO 8601
    tool: str                       # "network_audit", "cdp", etc
    action: str                     # "read", "write", "validate", etc
    input_params: str               # JSON dump of parameters
    input_hash: str                 # SHA256 of parameters (for dedup)
    decision: str                   # "ALLOW" or "DENY"
    reason: str                     # Why this decision was made
    severity: str                   # "info", "warning", "critical"
    user_agent: Optional[str]       # Which agent/process initiated
    bypass_attempt: bool            # Flag: was this a bypass attempt?

    def to_dict(self) -> dict:
        return asdict(self)


class AuditLogger:
    """
    Thread-safe audit logger with SQLite backend.

    Usage:
        audit = AuditLogger(db_path="~/.beigebox/audit.db")
        audit.log_validation(
            tool="network_audit",
            action="scan_network",
            params={"subnet": "192.168.1.0/24"},
            decision="ALLOW",
            reason="Subnet within RFC1918"
        )

        # Query recent denials
        denials = audit.search_denials(severity="critical", limit=100)
    """

    def __init__(self, db_path: str | Path = None):
        self.db_path = Path(db_path or "~/.beigebox/audit.db").expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Thread-safe queue
        self._lock = threading.Lock()

        # Initialize database
        self._init_db()

        logger.info(f"AuditLogger initialized: {self.db_path}")

    def _init_db(self):
        """Create audit table if it doesn't exist."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    action TEXT NOT NULL,
                    input_params TEXT,
                    input_hash TEXT UNIQUE,
                    decision TEXT NOT NULL,
                    reason TEXT,
                    severity TEXT,
                    user_agent TEXT,
                    bypass_attempt BOOLEAN DEFAULT 0
                )
            """)

            # Index on frequently queried columns
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_log(timestamp DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_decision ON audit_log(decision)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bypass ON audit_log(bypass_attempt)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tool ON audit_log(tool)
            """)
            conn.commit()

    def log_validation(
        self,
        tool: str,
        action: str,
        params: dict | str,
        decision: str,
        reason: str,
        severity: str = "info",
        user_agent: Optional[str] = None,
        bypass_attempt: bool = False,
    ) -> None:
        """
        Log a validation decision.

        Args:
            tool: Tool name (e.g., "network_audit")
            action: Action being validated (e.g., "read")
            params: Input parameters (dict or JSON string)
            decision: "ALLOW" or "DENY"
            reason: Why this decision was made
            severity: "info", "warning", "critical"
            user_agent: Optional identifier of who made this call
            bypass_attempt: True if this looks like a bypass attempt
        """

        # Serialize params
        if isinstance(params, dict):
            params_json = json.dumps(params, default=str, sort_keys=True)
        else:
            params_json = str(params)

        # Hash for dedup
        params_hash = hashlib.sha256(params_json.encode()).hexdigest()

        entry = AuditLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            tool=tool,
            action=action,
            input_params=params_json,
            input_hash=params_hash,
            decision=decision.upper(),
            reason=reason,
            severity=severity,
            user_agent=user_agent,
            bypass_attempt=bypass_attempt,
        )

        self._insert_log(entry)

        # Also log to Python logger
        level = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "critical": logging.CRITICAL,
        }.get(severity, logging.INFO)

        bypass_marker = "⚠️  POTENTIAL BYPASS" if bypass_attempt else ""
        logger.log(
            level,
            f"[AUDIT] {entry.decision} {entry.tool}/{entry.action} - {entry.reason} {bypass_marker}"
        )

    def _insert_log(self, entry: AuditLogEntry) -> None:
        """Insert log entry into database."""
        with self._lock:
            try:
                with sqlite3.connect(str(self.db_path)) as conn:
                    conn.execute("""
                        INSERT INTO audit_log (
                            timestamp, tool, action, input_params, input_hash,
                            decision, reason, severity, user_agent, bypass_attempt
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry.timestamp,
                        entry.tool,
                        entry.action,
                        entry.input_params,
                        entry.input_hash,
                        entry.decision,
                        entry.reason,
                        entry.severity,
                        entry.user_agent,
                        entry.bypass_attempt,
                    ))
                    conn.commit()
            except sqlite3.IntegrityError:
                # Duplicate input_hash (same params submitted multiple times)
                # This is interesting for bypass detection
                logger.debug(f"Duplicate audit entry: {entry.input_hash}")
            except Exception as e:
                logger.error(f"Failed to insert audit log: {e}")

    def search_denials(
        self,
        severity: Optional[str] = None,
        tool: Optional[str] = None,
        limit: int = 100,
        hours: int = 24,
    ) -> list[dict]:
        """
        Search recent DENY decisions.

        Useful for finding attack patterns.
        """
        conditions = ["decision = 'DENY'"]
        params: list = []

        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        if tool:
            conditions.append("tool = ?")
            params.append(tool)

        # Bound numerics so a caller can't pass weird values (negative, huge).
        hours = max(0, int(hours))
        limit = max(0, min(int(limit), 10_000))

        # Last N hours — `hours` is now an int, safe to format into the
        # datetime() literal (sqlite has no parameter binding for modifiers).
        conditions.append(f"datetime(timestamp) > datetime('now', '-{hours} hours')")
        where_clause = " AND ".join(conditions)

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM audit_log WHERE {where_clause} "
                "ORDER BY timestamp DESC LIMIT ?",
                (*params, limit),
            ).fetchall()

            return [dict(row) for row in rows]

    def search_bypass_attempts(
        self,
        limit: int = 100,
    ) -> list[dict]:
        """
        Search for potential bypass attempts.

        Returns entries flagged as bypass_attempt=True.
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM audit_log
                WHERE bypass_attempt = 1
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()

            return [dict(row) for row in rows]

    def search_suspicious_patterns(
        self,
        hours: int = 24,
        threshold: int = 5,
    ) -> list[dict]:
        """
        Find patterns suggesting attack:
          - Same tool called with many different inputs, all denied
          - Rapid-fire calls with slight variations (fuzzing)
          - Calls from multiple agents in short time
        """
        results = []

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row

            # Pattern 1: Tool receiving many DENY decisions
            many_denials = conn.execute("""
                SELECT
                    tool,
                    action,
                    COUNT(*) as denial_count,
                    COUNT(DISTINCT input_hash) as unique_inputs
                FROM audit_log
                WHERE decision = 'DENY'
                  AND datetime(timestamp) > datetime('now', '-' || ? || ' hours')
                GROUP BY tool, action
                HAVING COUNT(*) > ?
                ORDER BY denial_count DESC
            """, (hours, threshold)).fetchall()

            for row in many_denials:
                results.append({
                    "pattern": "MANY_DENIALS",
                    "tool": row["tool"],
                    "action": row["action"],
                    "denial_count": row["denial_count"],
                    "unique_inputs": row["unique_inputs"],
                })

            # Pattern 2: Rapid calls (>100/min)
            rapid_calls = conn.execute("""
                SELECT
                    tool,
                    strftime('%Y-%m-%d %H:%M', timestamp) as minute,
                    COUNT(*) as call_count
                FROM audit_log
                WHERE datetime(timestamp) > datetime('now', '-' || ? || ' hours')
                GROUP BY tool, minute
                HAVING COUNT(*) > 100
                ORDER BY call_count DESC
            """, (hours,)).fetchall()

            for row in rapid_calls:
                results.append({
                    "pattern": "RAPID_CALLS",
                    "tool": row["tool"],
                    "minute": row["minute"],
                    "call_count": row["call_count"],
                })

        return results

    def get_stats(self, hours: int = 24) -> dict:
        """Get summary statistics."""
        with sqlite3.connect(str(self.db_path)) as conn:
            stats = conn.execute("""
                SELECT
                    COUNT(*) as total_calls,
                    SUM(CASE WHEN decision = 'ALLOW' THEN 1 ELSE 0 END) as allowed,
                    SUM(CASE WHEN decision = 'DENY' THEN 1 ELSE 0 END) as denied,
                    SUM(CASE WHEN bypass_attempt = 1 THEN 1 ELSE 0 END) as bypass_attempts,
                    SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) as critical_issues
                FROM audit_log
                WHERE datetime(timestamp) > datetime('now', '-' || ? || ' hours')
            """, (hours,)).fetchone()

            if stats:
                return {
                    "total_calls": stats[0],
                    "allowed": stats[1],
                    "denied": stats[2],
                    "bypass_attempts": stats[3],
                    "critical_issues": stats[4],
                    "allow_rate": f"{100 * (stats[1] or 0) / max(stats[0] or 1, 1):.1f}%",
                }
            return {
                "total_calls": 0,
                "allowed": 0,
                "denied": 0,
                "bypass_attempts": 0,
                "critical_issues": 0,
            }
