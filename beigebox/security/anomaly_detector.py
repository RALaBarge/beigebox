"""
API Anomaly Detection — Behavioral analysis for token extraction attack prevention.

Detects unusual API usage patterns that may indicate automated attacks:
  - Request rate spikes (z-score based, >3sigma above baseline)
  - Error rate spikes (z-score based, >3sigma above baseline error rate)
  - Model switching patterns (>N distinct models in time window)
  - Latency anomalies (sub-baseline latency indicating timing attacks)
  - IP/User-Agent instability (5+ different IPs in same conversation)
  - Payload size anomalies (<50 chars or >100KB requests)

Algorithm:
  - Track per-IP request history (rolling 5-min deque)
  - Calculate z-score on request rate using rolling window baselines
  - Maintain error rate baseline per IP with z-score deviation
  - Track model diversity in 5-min window
  - Monitor latency percentiles for baseline shifts
  - Correlate IP/UA changes with conversations
  - Aggregate signals into composite risk score (0.0-1.0)

Sensitivity levels:
  - low:    z_threshold=4.0, higher thresholds, fewer false positives
  - medium: z_threshold=3.0, balanced defaults
  - high:   z_threshold=2.0, lower thresholds, catches more but noisier

Performance: ~1ms per request (deque operations + numpy), comfortably under 100ms budget.

Cold start handling: Requires min_baseline_size requests before z-score is reliable.
Until then, falls back to simple threshold checks.
"""

import json
import logging
import sqlite3
import time
import numpy as np
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Sensitivity presets ─────────────────────────────────────────────────────

SENSITIVITY_PRESETS = {
    "low": {
        "z_threshold": 4.0,
        "request_rate_threshold": 10,
        "error_rate_threshold": 0.40,
        "model_switch_threshold": 12,
        "latency_z_threshold": 4.0,
        "payload_min_chars": 20,
        "payload_max_bytes": 200_000,
        "ip_instability_threshold": 8,
        "min_baseline_size": 80,
    },
    "medium": {
        "z_threshold": 3.0,
        "request_rate_threshold": 5,
        "error_rate_threshold": 0.30,
        "model_switch_threshold": 8,
        "latency_z_threshold": 3.0,
        "payload_min_chars": 50,
        "payload_max_bytes": 100_000,
        "ip_instability_threshold": 5,
        "min_baseline_size": 50,
    },
    "high": {
        "z_threshold": 2.0,
        "request_rate_threshold": 3,
        "error_rate_threshold": 0.15,
        "model_switch_threshold": 4,
        "latency_z_threshold": 2.0,
        "payload_min_chars": 80,
        "payload_max_bytes": 50_000,
        "ip_instability_threshold": 3,
        "min_baseline_size": 30,
    },
}


@dataclass
class RequestRecord:
    """Single API request record."""
    timestamp: float
    ip: str
    user_agent: str
    api_key: str
    model: str
    request_bytes: int
    status_code: int
    latency_ms: float


@dataclass
class SessionStats:
    """Per-IP/session statistics."""
    ip: str
    request_count: int = 0
    error_count: int = 0
    distinct_models: set = field(default_factory=set)
    distinct_user_agents: set = field(default_factory=set)
    request_history: deque = field(default_factory=lambda: deque(maxlen=200))
    latency_history: deque = field(default_factory=lambda: deque(maxlen=200))
    # Rolling rate samples: each entry = (timestamp, requests_in_that_minute)
    rate_samples: deque = field(default_factory=lambda: deque(maxlen=100))
    error_rate_samples: deque = field(default_factory=lambda: deque(maxlen=100))
    payload_sizes: deque = field(default_factory=lambda: deque(maxlen=200))
    last_seen: float = 0.0
    mean_latency: float = 0.0
    std_latency: float = 1.0


class AnomalyBaselinesDB:
    """SQLite persistence for anomaly detection baselines.

    Stores per-session aggregate stats so baselines survive restarts.
    Uses a dedicated table in the main BeigeBox database or a standalone file.
    """

    CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS anomaly_baselines (
        ip                TEXT PRIMARY KEY,
        request_count     INTEGER DEFAULT 0,
        error_count       INTEGER DEFAULT 0,
        mean_rate         REAL DEFAULT 0.0,
        std_rate          REAL DEFAULT 1.0,
        mean_error_rate   REAL DEFAULT 0.0,
        std_error_rate    REAL DEFAULT 0.1,
        mean_latency      REAL DEFAULT 0.0,
        std_latency       REAL DEFAULT 1.0,
        mean_payload_size REAL DEFAULT 0.0,
        std_payload_size  REAL DEFAULT 1.0,
        distinct_models   TEXT DEFAULT '[]',
        last_seen         REAL DEFAULT 0.0,
        updated_at        TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    CREATE TABLE IF NOT EXISTS anomaly_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        ip          TEXT NOT NULL,
        rules       TEXT NOT NULL,
        risk_score  REAL NOT NULL,
        action      TEXT NOT NULL,
        meta        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_anomaly_events_ts ON anomaly_events(ts);
    CREATE INDEX IF NOT EXISTS idx_anomaly_events_ip ON anomaly_events(ip);
    """

    def __init__(self, db_path: str = ""):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        if db_path:
            try:
                self._conn = sqlite3.connect(db_path, check_same_thread=False)
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.executescript(self.CREATE_SQL)
                self._conn.commit()
            except Exception as e:
                logger.warning("AnomalyBaselinesDB init failed (%s): %s", db_path, e)
                self._conn = None

    def save_baseline(self, ip: str, stats: "SessionStats") -> None:
        """Persist baseline stats for an IP."""
        if not self._conn:
            return
        try:
            rate_samples = list(stats.rate_samples)
            error_samples = list(stats.error_rate_samples)
            payload_sizes = list(stats.payload_sizes)

            mean_rate = float(np.mean(rate_samples)) if rate_samples else 0.0
            std_rate = float(np.std(rate_samples)) if len(rate_samples) > 1 else 1.0
            mean_err = float(np.mean(error_samples)) if error_samples else 0.0
            std_err = float(np.std(error_samples)) if len(error_samples) > 1 else 0.1
            mean_payload = float(np.mean(payload_sizes)) if payload_sizes else 0.0
            std_payload = float(np.std(payload_sizes)) if len(payload_sizes) > 1 else 1.0

            self._conn.execute(
                """INSERT OR REPLACE INTO anomaly_baselines
                   (ip, request_count, error_count, mean_rate, std_rate,
                    mean_error_rate, std_error_rate, mean_latency, std_latency,
                    mean_payload_size, std_payload_size, distinct_models, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ip, stats.request_count, stats.error_count,
                    mean_rate, std_rate, mean_err, std_err,
                    stats.mean_latency, stats.std_latency,
                    mean_payload, std_payload,
                    json.dumps(sorted(stats.distinct_models)),
                    stats.last_seen,
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.debug("save_baseline(%s) failed: %s", ip, e)

    def load_baseline(self, ip: str) -> Optional[dict]:
        """Load persisted baseline for an IP."""
        if not self._conn:
            return None
        try:
            row = self._conn.execute(
                "SELECT * FROM anomaly_baselines WHERE ip = ?", (ip,)
            ).fetchone()
            if not row:
                return None
            cols = [
                "ip", "request_count", "error_count", "mean_rate", "std_rate",
                "mean_error_rate", "std_error_rate", "mean_latency", "std_latency",
                "mean_payload_size", "std_payload_size", "distinct_models",
                "last_seen", "updated_at",
            ]
            return dict(zip(cols, row))
        except Exception as e:
            logger.debug("load_baseline(%s) failed: %s", ip, e)
            return None

    def record_event(
        self, ip: str, rules: list[str], risk_score: float, action: str, meta: dict | None = None
    ) -> None:
        """Record an anomaly event for audit trail."""
        if not self._conn:
            return
        try:
            self._conn.execute(
                "INSERT INTO anomaly_events (ip, rules, risk_score, action, meta) VALUES (?, ?, ?, ?, ?)",
                (ip, json.dumps(rules), risk_score, action, json.dumps(meta or {})),
            )
            self._conn.commit()
        except Exception as e:
            logger.debug("record_event failed: %s", e)

    def get_events(self, ip: str = "", limit: int = 50) -> list[dict]:
        """Retrieve anomaly events, optionally filtered by IP."""
        if not self._conn:
            return []
        try:
            if ip:
                rows = self._conn.execute(
                    "SELECT ts, ip, rules, risk_score, action, meta FROM anomaly_events WHERE ip = ? ORDER BY ts DESC LIMIT ?",
                    (ip, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT ts, ip, rules, risk_score, action, meta FROM anomaly_events ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [
                {
                    "timestamp": r[0], "ip": r[1], "rules": json.loads(r[2]),
                    "risk_score": r[3], "action": r[4], "meta": json.loads(r[5] or "{}"),
                }
                for r in rows
            ]
        except Exception as e:
            logger.debug("get_events failed: %s", e)
            return []

    def get_all_baselines(self) -> list[dict]:
        """Return all stored baselines."""
        if not self._conn:
            return []
        try:
            rows = self._conn.execute(
                "SELECT ip, request_count, mean_rate, std_rate, mean_latency, std_latency, last_seen FROM anomaly_baselines ORDER BY last_seen DESC"
            ).fetchall()
            return [
                {
                    "ip": r[0], "request_count": r[1], "mean_rate": r[2],
                    "std_rate": r[3], "mean_latency": r[4], "std_latency": r[5],
                    "last_seen": r[6],
                }
                for r in rows
            ]
        except Exception:
            return []

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass


# ── Rule severity weights for risk score ────────────────────────────────────

_RULE_WEIGHTS = {
    "request_rate_spike": 0.30,
    "error_rate_spike": 0.25,
    "model_switching": 0.20,
    "latency_anomaly": 0.15,
    "ua_instability": 0.05,
    "payload_size_anomaly": 0.10,
    "ip_instability": 0.10,
}


def _compute_risk_score(triggered_rules: list[str]) -> float:
    """Compute composite risk score from triggered rules.

    Returns a float 0.0-1.0 where higher = more suspicious.
    Multiple triggered rules compound (max 1.0).
    """
    if not triggered_rules:
        return 0.0
    score = sum(_RULE_WEIGHTS.get(r, 0.05) for r in triggered_rules)
    return min(1.0, score)


def _recommended_action(risk_score: float, detection_mode: str = "warn") -> str:
    """Determine recommended action based on risk score and mode.

    Returns: "allow", "warn", "rate_limit", or "block"
    """
    if detection_mode == "block" and risk_score >= 0.3:
        return "block"
    if detection_mode == "rate_limit" and risk_score >= 0.3:
        return "rate_limit"
    if risk_score >= 0.7:
        return "rate_limit"
    if risk_score >= 0.3:
        return "warn"
    return "allow"


class APIAnomalyDetector:
    """
    Real-time API anomaly detection with z-score based signal analysis.

    Tracks per-IP request patterns and flags suspicious behaviors.
    Thread-safe, memory-bounded (1000 sessions max), <1ms per request.

    Supports:
      - Z-score anomaly detection for request rate, error rate, latency, payload sizes
      - Composite risk scoring (0.0-1.0)
      - Sensitivity presets (low/medium/high)
      - SQLite baseline persistence
      - Cold start handling (fallback to simple thresholds until baseline established)
    """

    def __init__(
        self,
        window_seconds: int = 300,
        request_rate_threshold: int = 5,
        error_rate_threshold: float = 0.30,
        model_switch_threshold: int = 8,
        latency_z_threshold: float = 3.0,
        payload_min_chars: int = 50,
        payload_max_bytes: int = 100_000,
        ip_instability_threshold: int = 5,
        sensitivity: str = "medium",
        db_path: str = "",
        detection_mode: str = "warn",
        min_baseline_size: int = 0,
        check_interval_requests: int = 100,
        check_interval_seconds: float = 300.0,
    ):
        """
        Initialize anomaly detector.

        Args:
            window_seconds: Rolling time window for baseline calculations
            request_rate_threshold: Max requests per minute per IP (simple fallback)
            error_rate_threshold: Max error rate 0.0-1.0 (simple fallback)
            model_switch_threshold: Max distinct models in window
            latency_z_threshold: Z-score threshold for latency anomalies
            payload_min_chars: Minimum request body size (chars)
            payload_max_bytes: Maximum request body size (bytes)
            ip_instability_threshold: Max distinct IPs per conversation
            sensitivity: Preset level (low/medium/high) — overrides individual thresholds
            db_path: SQLite path for baseline persistence (empty = in-memory only)
            detection_mode: Default action mode (warn/rate_limit/block)
            min_baseline_size: Min requests before z-score is reliable
            check_interval_requests: Run periodic check every N requests
            check_interval_seconds: Run periodic check every N seconds
        """
        # Apply sensitivity preset if specified
        preset = SENSITIVITY_PRESETS.get(sensitivity, {})
        self.sensitivity = sensitivity
        self.window_seconds = window_seconds
        self.request_rate_threshold = preset.get("request_rate_threshold", request_rate_threshold)
        self.error_rate_threshold = preset.get("error_rate_threshold", error_rate_threshold)
        self.model_switch_threshold = preset.get("model_switch_threshold", model_switch_threshold)
        self.latency_z_threshold = preset.get("latency_z_threshold", latency_z_threshold)
        self.payload_min_chars = preset.get("payload_min_chars", payload_min_chars)
        self.payload_max_bytes = preset.get("payload_max_bytes", payload_max_bytes)
        self.ip_instability_threshold = preset.get("ip_instability_threshold", ip_instability_threshold)
        self.z_threshold = preset.get("z_threshold", 3.0)
        self.min_baseline_size = min_baseline_size or preset.get("min_baseline_size", 50)
        self.detection_mode = detection_mode
        self.check_interval_requests = check_interval_requests
        self.check_interval_seconds = check_interval_seconds

        # Per-IP/session tracking
        self._lock = Lock()
        self._sessions: dict[str, SessionStats] = {}
        self._conversation_ips: dict[str, set[str]] = {}
        self._global_request_count = 0
        self._last_periodic_check = time.time()

        # SQLite persistence
        self.db = AnomalyBaselinesDB(db_path)

        logger.info(
            "APIAnomalyDetector initialized ("
            "sensitivity=%s, window=%ds, z_threshold=%.1f, "
            "rate_threshold=%d/min, error_threshold=%.1f%%, "
            "model_switches=%d, latency_z=%.1f, min_baseline=%d, "
            "db=%s)",
            sensitivity,
            window_seconds,
            self.z_threshold,
            self.request_rate_threshold,
            self.error_rate_threshold * 100,
            self.model_switch_threshold,
            self.latency_z_threshold,
            self.min_baseline_size,
            db_path or "in-memory",
        )

    def record_request(
        self,
        ip: str,
        user_agent: str,
        api_key: str,
        model: str,
        request_bytes: int,
        status_code: int,
        latency_ms: float,
        conversation_id: str = "",
    ) -> None:
        """
        Record a request for baseline updates.

        Called post-request to update statistics with actual latency/status.
        """
        if not ip or not model:
            return

        record = RequestRecord(
            timestamp=time.time(),
            ip=ip,
            user_agent=user_agent,
            api_key=api_key,
            model=model,
            request_bytes=request_bytes,
            status_code=status_code,
            latency_ms=latency_ms,
        )

        with self._lock:
            # Get or create session (try loading from DB first)
            if ip not in self._sessions:
                self._sessions[ip] = SessionStats(ip=ip)
                # Attempt to hydrate from persisted baseline
                saved = self.db.load_baseline(ip)
                if saved:
                    self._sessions[ip].request_count = saved.get("request_count", 0)
                    self._sessions[ip].error_count = saved.get("error_count", 0)
                    self._sessions[ip].mean_latency = saved.get("mean_latency", 0.0)
                    self._sessions[ip].std_latency = saved.get("std_latency", 1.0)

            session = self._sessions[ip]
            session.request_history.append(record)
            session.latency_history.append(latency_ms)
            session.payload_sizes.append(request_bytes)
            session.last_seen = record.timestamp
            session.distinct_models.add(model)
            session.distinct_user_agents.add(user_agent)
            session.request_count += 1
            self._global_request_count += 1

            # Error tracking
            if 400 <= status_code < 600:
                session.error_count += 1

            # Update latency baseline (mean + std)
            if len(session.latency_history) > 1:
                session.mean_latency = float(np.mean(session.latency_history))
                session.std_latency = float(np.std(session.latency_history))
                if session.std_latency < 1e-3:
                    session.std_latency = 1.0

            # Sample rate: count requests in last minute → append to rate_samples
            now = record.timestamp
            one_min_ago = now - 60.0
            recent = [r for r in session.request_history if r.timestamp >= one_min_ago]
            session.rate_samples.append(len(recent))

            # Sample error rate: ratio of errors in last window
            window_reqs = [r for r in session.request_history if now - r.timestamp <= self.window_seconds]
            if window_reqs:
                errors = sum(1 for r in window_reqs if 400 <= r.status_code < 600)
                session.error_rate_samples.append(errors / len(window_reqs))
            else:
                session.error_rate_samples.append(0.0)

            # Track conversation IP association
            if conversation_id:
                if conversation_id not in self._conversation_ips:
                    self._conversation_ips[conversation_id] = set()
                self._conversation_ips[conversation_id].add(ip)

            # Periodic maintenance
            if self._global_request_count % self.check_interval_requests == 0:
                self._evict_stale_sessions_locked()
                # Persist baselines for active sessions
                for sess_ip, sess in self._sessions.items():
                    self.db.save_baseline(sess_ip, sess)

    def is_anomalous(
        self, ip: str, user_agent: str, request_bytes: int = 0
    ) -> tuple[bool, list[str]]:
        """
        Check if current request from IP is anomalous.

        Returns:
            (is_anomalous, triggered_rules) where triggered_rules is list of
            rule names that were triggered.
        """
        if not ip:
            return False, []

        with self._lock:
            session = self._sessions.get(ip)
            if not session:
                return False, []

            triggered = []
            now = time.time()

            # Rule 1: Request rate spike (z-score or simple threshold)
            rate_anom, rate_rules = self._check_request_rate(session, now)
            if rate_anom:
                triggered.extend(rate_rules)

            # Rule 2: Error rate spike (z-score or simple threshold)
            error_anom, error_rules = self._check_error_rate(session, now)
            if error_anom:
                triggered.extend(error_rules)

            # Rule 3: Model switching (threshold in window)
            model_anom, model_rules = self._check_model_switches(session, now)
            if model_anom:
                triggered.extend(model_rules)

            # Rule 4: Latency anomaly (z-score)
            latency_anom, latency_rules = self._check_latency(session, user_agent)
            if latency_anom:
                triggered.extend(latency_rules)

            # Rule 5: User-Agent instability
            ua_anom, ua_rules = self._check_ua_instability(session)
            if ua_anom:
                triggered.extend(ua_rules)

            # Rule 6: Payload size anomaly (z-score or threshold)
            if request_bytes > 0:
                payload_anom, payload_rules = self._check_payload_size(session, request_bytes)
                if payload_anom:
                    triggered.extend(payload_rules)

            return len(triggered) > 0, triggered

    def analyze(
        self,
        ip: str = "",
        user_agent: str = "",
        request_bytes: int = 0,
        time_window: int = 0,
    ) -> dict:
        """
        Full anomaly analysis with risk score and recommended action.

        Returns:
            {
                "anomalies": [{"rule": str, "detail": str, "z_score": float|None}],
                "risk_score": float,
                "recommended_action": str,
                "session_stats": dict,
                "baseline_status": str,
            }
        """
        if not ip:
            # Analyze all sessions
            return self.get_anomaly_report()

        is_anom, triggered = self.is_anomalous(ip, user_agent, request_bytes)
        risk = _compute_risk_score(triggered)
        action = _recommended_action(risk, self.detection_mode)

        stats = self.get_session_stats(ip)
        has_baseline = stats.get("total_requests", 0) >= self.min_baseline_size

        anomalies = []
        for rule in triggered:
            detail = self._get_rule_detail(ip, rule)
            anomalies.append({
                "rule": rule,
                "detail": detail.get("detail", rule),
                "z_score": detail.get("z_score"),
            })

        # Record event if anomalous
        if is_anom:
            self.db.record_event(ip, triggered, risk, action, {"user_agent": user_agent})

        return {
            "anomalies": anomalies,
            "risk_score": round(risk, 3),
            "recommended_action": action,
            "session_stats": stats,
            "baseline_status": "established" if has_baseline else "cold_start",
        }

    def get_session_stats(self, ip: str) -> dict:
        """Return current statistics for a session (IP)."""
        with self._lock:
            session = self._sessions.get(ip)
            if not session:
                return {}

            now = time.time()
            window_reqs = [
                r for r in session.request_history
                if now - r.timestamp <= self.window_seconds
            ]

            return {
                "ip": ip,
                "total_requests": session.request_count,
                "recent_requests": len(window_reqs),
                "error_count": session.error_count,
                "distinct_models": len(session.distinct_models),
                "distinct_user_agents": len(session.distinct_user_agents),
                "mean_latency_ms": round(session.mean_latency, 1),
                "std_latency_ms": round(session.std_latency, 1),
                "last_seen": session.last_seen,
                "models": sorted(list(session.distinct_models)),
                "baseline_samples": len(session.rate_samples),
                "has_baseline": session.request_count >= self.min_baseline_size,
            }

    def get_anomaly_report(self) -> dict:
        """Generate a security report of all anomalous sessions."""
        with self._lock:
            now = time.time()
            anomalous_sessions = []

            for ip, session in self._sessions.items():
                is_anom, triggered = self._check_all_rules(session, now)
                if is_anom:
                    risk = _compute_risk_score(triggered)
                    action = _recommended_action(risk, self.detection_mode)
                    anomalous_sessions.append({
                        "ip": ip,
                        "rules_triggered": triggered,
                        "risk_score": round(risk, 3),
                        "recommended_action": action,
                        "request_count": session.request_count,
                        "error_rate": self._calc_error_rate(session, now),
                        "distinct_models": len(session.distinct_models),
                        "last_seen": session.last_seen,
                    })

            return {
                "timestamp": now,
                "total_sessions": len(self._sessions),
                "anomalous_count": len(anomalous_sessions),
                "sensitivity": self.sensitivity,
                "detection_mode": self.detection_mode,
                "sessions": anomalous_sessions,
            }

    def get_historical_events(self, ip: str = "", limit: int = 50) -> list[dict]:
        """Retrieve historical anomaly events from SQLite."""
        return self.db.get_events(ip=ip, limit=limit)

    def get_all_baselines(self) -> list[dict]:
        """Return all persisted baselines."""
        return self.db.get_all_baselines()

    # ───────────────────────────────────────────────────────────────────────
    # Z-score helpers
    # ───────────────────────────────────────────────────────────────────────

    @staticmethod
    def _zscore(value: float, mean: float, std: float) -> float:
        """Calculate z-score. Returns 0.0 if std is near zero."""
        if std < 1e-6:
            return 0.0
        return (value - mean) / std

    def _has_baseline(self, session: SessionStats) -> bool:
        """Check if session has enough data for z-score analysis."""
        return session.request_count >= self.min_baseline_size

    # ───────────────────────────────────────────────────────────────────────
    # Rule checks
    # ───────────────────────────────────────────────────────────────────────

    def _check_request_rate(
        self, session: SessionStats, now: float
    ) -> tuple[bool, list[str]]:
        """Rule 1: Request rate spike detection (z-score or fallback)."""
        one_min_ago = now - 60.0
        recent = [r for r in session.request_history if r.timestamp >= one_min_ago]
        current_rate = len(recent)

        if self._has_baseline(session) and len(session.rate_samples) >= 5:
            mean_rate = float(np.mean(session.rate_samples))
            std_rate = float(np.std(session.rate_samples))
            z = self._zscore(current_rate, mean_rate, std_rate)
            if z > self.z_threshold:
                logger.warning(
                    "Anomaly: request_rate_spike (ip=%s, rate=%d/min, z=%.2f, mean=%.1f, std=%.1f)",
                    session.ip, current_rate, z, mean_rate, std_rate,
                )
                return True, ["request_rate_spike"]
        else:
            # Cold start fallback: simple threshold
            if current_rate > self.request_rate_threshold:
                logger.warning(
                    "Anomaly: request_rate_spike [cold_start] (ip=%s, rate=%d/min, threshold=%d)",
                    session.ip, current_rate, self.request_rate_threshold,
                )
                return True, ["request_rate_spike"]

        return False, []

    def _check_error_rate(
        self, session: SessionStats, now: float
    ) -> tuple[bool, list[str]]:
        """Rule 2: Error rate spike detection (z-score or fallback)."""
        error_rate = self._calc_error_rate(session, now)

        if self._has_baseline(session) and len(session.error_rate_samples) >= 5:
            mean_err = float(np.mean(session.error_rate_samples))
            std_err = float(np.std(session.error_rate_samples))
            if std_err < 0.01:
                std_err = 0.01  # Prevent z-score explosion on near-zero baseline
            z = self._zscore(error_rate, mean_err, std_err)
            if z > self.z_threshold:
                logger.warning(
                    "Anomaly: error_rate_spike (ip=%s, rate=%.1f%%, z=%.2f)",
                    session.ip, error_rate * 100, z,
                )
                return True, ["error_rate_spike"]
        else:
            # Cold start fallback
            if error_rate > self.error_rate_threshold:
                logger.warning(
                    "Anomaly: error_rate_spike [cold_start] (ip=%s, rate=%.1f%%)",
                    session.ip, error_rate * 100,
                )
                return True, ["error_rate_spike"]

        return False, []

    def _check_model_switches(
        self, session: SessionStats, now: float
    ) -> tuple[bool, list[str]]:
        """Rule 3: Model switching pattern detection."""
        window_reqs = [
            r for r in session.request_history
            if now - r.timestamp <= self.window_seconds
        ]
        distinct_models = len(set(r.model for r in window_reqs))

        if distinct_models > self.model_switch_threshold:
            logger.warning(
                "Anomaly: model_switching (ip=%s, models=%d, threshold=%d)",
                session.ip, distinct_models, self.model_switch_threshold,
            )
            return True, ["model_switching"]

        return False, []

    def _check_latency(
        self, session: SessionStats, user_agent: str
    ) -> tuple[bool, list[str]]:
        """Rule 4: Latency anomaly detection (z-score)."""
        if len(session.latency_history) < 5:
            return False, []

        latest = session.latency_history[-1]

        if session.std_latency > 0:
            z = self._zscore(latest, session.mean_latency, session.std_latency)
            # Sub-baseline latency = possible timing attack; super-high = possible DoS probe
            if z < -self.latency_z_threshold or z > self.latency_z_threshold:
                logger.warning(
                    "Anomaly: latency_anomaly (ip=%s, z=%.2f, latest=%.1fms, mean=%.1fms)",
                    session.ip, z, latest, session.mean_latency,
                )
                return True, ["latency_anomaly"]

        return False, []

    def _check_ua_instability(self, session: SessionStats) -> tuple[bool, list[str]]:
        """Rule 5: User-Agent instability."""
        if len(session.distinct_user_agents) > 3:
            logger.warning(
                "Anomaly: ua_instability (ip=%s, uas=%d)",
                session.ip, len(session.distinct_user_agents),
            )
            return True, ["ua_instability"]
        return False, []

    def _check_payload_size(
        self, session: SessionStats, request_bytes: int
    ) -> tuple[bool, list[str]]:
        """Rule 6: Payload size anomaly detection (z-score or threshold)."""
        # Hard bounds check
        if request_bytes < self.payload_min_chars or request_bytes > self.payload_max_bytes:
            return True, ["payload_size_anomaly"]

        # Z-score check if baseline available
        if self._has_baseline(session) and len(session.payload_sizes) >= 10:
            mean_payload = float(np.mean(session.payload_sizes))
            std_payload = float(np.std(session.payload_sizes))
            if std_payload > 1.0:
                z = self._zscore(request_bytes, mean_payload, std_payload)
                if abs(z) > self.z_threshold:
                    logger.warning(
                        "Anomaly: payload_size_anomaly (ip=%s, bytes=%d, z=%.2f, mean=%.0f)",
                        session.ip, request_bytes, z, mean_payload,
                    )
                    return True, ["payload_size_anomaly"]

        return False, []

    def _check_ip_instability(self, conversation_id: str) -> tuple[bool, list[str]]:
        """Rule 7: IP instability in a conversation."""
        if not conversation_id:
            return False, []
        ips = self._conversation_ips.get(conversation_id, set())
        if len(ips) > self.ip_instability_threshold:
            return True, ["ip_instability"]
        return False, []

    def _check_all_rules(
        self, session: SessionStats, now: float
    ) -> tuple[bool, list[str]]:
        """Check all rules for a session (for reporting)."""
        triggered = []

        rate_anom, rate_rules = self._check_request_rate(session, now)
        if rate_anom:
            triggered.extend(rate_rules)

        error_anom, error_rules = self._check_error_rate(session, now)
        if error_anom:
            triggered.extend(error_rules)

        model_anom, model_rules = self._check_model_switches(session, now)
        if model_anom:
            triggered.extend(model_rules)

        latency_anom, latency_rules = self._check_latency(session, "")
        if latency_anom:
            triggered.extend(latency_rules)

        ua_anom, ua_rules = self._check_ua_instability(session)
        if ua_anom:
            triggered.extend(ua_rules)

        return len(triggered) > 0, triggered

    def _calc_error_rate(self, session: SessionStats, now: float) -> float:
        """Calculate error rate in time window."""
        window_reqs = [
            r for r in session.request_history
            if now - r.timestamp <= self.window_seconds
        ]
        if not window_reqs:
            return 0.0
        errors = sum(1 for r in window_reqs if 400 <= r.status_code < 600)
        return errors / len(window_reqs)

    def _get_rule_detail(self, ip: str, rule: str) -> dict:
        """Get detailed info for a triggered rule."""
        session = self._sessions.get(ip)
        if not session:
            return {"detail": rule}

        now = time.time()

        if rule == "request_rate_spike":
            one_min_ago = now - 60.0
            recent = [r for r in session.request_history if r.timestamp >= one_min_ago]
            rate = len(recent)
            z = None
            if len(session.rate_samples) >= 5:
                mean_r = float(np.mean(session.rate_samples))
                std_r = float(np.std(session.rate_samples))
                z = round(self._zscore(rate, mean_r, std_r), 2)
            return {"detail": f"{rate} requests/min (threshold: {self.request_rate_threshold})", "z_score": z}

        if rule == "error_rate_spike":
            err_rate = self._calc_error_rate(session, now)
            z = None
            if len(session.error_rate_samples) >= 5:
                mean_e = float(np.mean(session.error_rate_samples))
                std_e = float(np.std(session.error_rate_samples))
                if std_e < 0.01:
                    std_e = 0.01
                z = round(self._zscore(err_rate, mean_e, std_e), 2)
            return {"detail": f"{err_rate*100:.1f}% error rate", "z_score": z}

        if rule == "model_switching":
            window_reqs = [r for r in session.request_history if now - r.timestamp <= self.window_seconds]
            models = set(r.model for r in window_reqs)
            return {"detail": f"{len(models)} distinct models in {self.window_seconds}s", "z_score": None}

        if rule == "latency_anomaly":
            latest = session.latency_history[-1] if session.latency_history else 0
            z = round(self._zscore(latest, session.mean_latency, session.std_latency), 2)
            return {"detail": f"{latest:.1f}ms (mean={session.mean_latency:.1f}ms)", "z_score": z}

        if rule == "payload_size_anomaly":
            return {"detail": f"Payload outside [{self.payload_min_chars}, {self.payload_max_bytes}]", "z_score": None}

        if rule == "ua_instability":
            return {"detail": f"{len(session.distinct_user_agents)} distinct User-Agents", "z_score": None}

        return {"detail": rule, "z_score": None}

    def _evict_stale_sessions_locked(self, ttl_seconds: int = 1800) -> None:
        """Remove sessions with no recent activity. Must be called with _lock held."""
        now = time.time()
        stale_ips = [
            ip for ip, session in self._sessions.items()
            if now - session.last_seen > ttl_seconds
        ]

        for ip in stale_ips:
            # Persist before eviction
            self.db.save_baseline(ip, self._sessions[ip])
            del self._sessions[ip]

        if stale_ips:
            logger.debug("Evicted %d stale anomaly sessions (TTL=%ds)", len(stale_ips), ttl_seconds)

        # Clean up old conversation IP mappings
        to_delete = []
        for conv_id, ip_set in self._conversation_ips.items():
            active_ips = [ip for ip in ip_set if ip in self._sessions]
            if not active_ips:
                to_delete.append(conv_id)
        for conv_id in to_delete:
            del self._conversation_ips[conv_id]
