"""
Flight Recorder — request lifecycle timelines.

Captures detailed milestones for each request through the proxy:
  message received → z-command → routing → backend → response → storage

In-memory ring buffer (max_records with retention). No persistence needed —
this is for live debugging, not historical analysis.
"""

from __future__ import annotations

import logging
import time
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


class FlightRecord:
    """Timeline for a single request through the proxy."""

    __slots__ = ("id", "conversation_id", "model", "start_time", "events", "_closed")

    def __init__(self, conversation_id: str = "", model: str = ""):
        self.id: str = uuid4().hex[:12]
        self.conversation_id = conversation_id
        self.model = model
        self.start_time: float = time.monotonic()
        self.events: list[dict] = []
        self._closed = False

    def log(self, stage: str, **details):
        """Record a milestone. Call this at each stage of the pipeline."""
        if self._closed:
            return
        elapsed = (time.monotonic() - self.start_time) * 1000
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round(elapsed, 2),
            "stage": stage,
        }
        if details:
            event["details"] = {k: v for k, v in details.items() if v is not None}
        self.events.append(event)

    def close(self):
        """Mark the record as complete."""
        if not self._closed:
            self.log("Complete")
            self._closed = True

    @property
    def total_ms(self) -> float:
        """Total elapsed time from first to last event."""
        if not self.events:
            return 0.0
        return self.events[-1]["elapsed_ms"]

    def summary(self) -> dict:
        """Compute breakdown by stage."""
        if len(self.events) < 2:
            return {"total_ms": self.total_ms}

        stages: dict[str, float] = {}
        for i in range(1, len(self.events)):
            stage = self.events[i]["stage"]
            delta = self.events[i]["elapsed_ms"] - self.events[i - 1]["elapsed_ms"]
            stages[stage] = stages.get(stage, 0) + delta

        total = self.total_ms or 1.0
        breakdown = {
            stage: {"ms": round(ms, 2), "pct": round(ms / total * 100, 1)}
            for stage, ms in sorted(stages.items(), key=lambda x: -x[1])
        }

        return {
            "total_ms": round(self.total_ms, 2),
            "breakdown": breakdown,
        }

    def to_json(self) -> dict:
        """Export full record as JSON."""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "model": self.model,
            "events": self.events,
            "summary": self.summary(),
        }

    def render_text(self) -> str:
        """Render as human-readable timeline."""
        lines = [f"FLIGHT RECORD: {self.id}"]
        if self.conversation_id:
            lines.append(f"Conversation: {self.conversation_id[:16]}")
        if self.model:
            lines.append(f"Model: {self.model}")
        lines.append("")

        for event in self.events:
            elapsed = event["elapsed_ms"]
            stage = event["stage"]
            line = f"  [{elapsed:8.1f}ms] {stage}"
            details = event.get("details", {})
            if details:
                detail_parts = [f"{k}={v}" for k, v in details.items()]
                line += f"  ({', '.join(detail_parts)})"
            lines.append(line)

        lines.append("")
        s = self.summary()
        lines.append(f"  TOTAL: {s['total_ms']:.0f}ms")
        for stage, info in s.get("breakdown", {}).items():
            lines.append(f"    {stage}: {info['ms']:.0f}ms ({info['pct']}%)")

        return "\n".join(lines)


class FlightRecorderStore:
    """
    In-memory store for flight records.
    Thread-safe LRU with max size and time-based retention.
    """

    def __init__(self, max_records: int = 1000, retention_hours: int = 24):
        self.max_records = max_records
        self.retention_seconds = retention_hours * 3600
        self._records: OrderedDict[str, FlightRecord] = OrderedDict()
        self._lock = threading.Lock()

    def store(self, record: FlightRecord):
        """Store a completed flight record."""
        with self._lock:
            # Evict oldest if at capacity
            while len(self._records) >= self.max_records:
                self._records.popitem(last=False)
            self._records[record.id] = record

    def get(self, record_id: str) -> FlightRecord | None:
        """Retrieve a flight record by ID."""
        with self._lock:
            return self._records.get(record_id)

    def recent(self, n: int = 10) -> list[FlightRecord]:
        """Get the N most recent records."""
        with self._lock:
            items = list(self._records.values())
        return items[-n:]

    def evict_stale(self):
        """Remove records older than retention period."""
        cutoff = time.monotonic() - self.retention_seconds
        with self._lock:
            stale = [
                rid for rid, rec in self._records.items()
                if rec.start_time < cutoff
            ]
            for rid in stale:
                del self._records[rid]
            if stale:
                logger.debug("Flight recorder evicted %d stale records", len(stale))

    @property
    def count(self) -> int:
        return len(self._records)
