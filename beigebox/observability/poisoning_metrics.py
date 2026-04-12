"""
RAG Poisoning Detection Metrics.

Tracks and exposes metrics for quarantined embeddings:
- quarantine_count_daily (gauge, resets daily)
- quarantine_confidence_{p50, p95, avg} (distribution of confidence scores)
- detector_method_breakdown (which layer triggered)
- quarantine_reason_breakdown (top reasons)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)


class PoisoningMetrics:
    """Metrics for RAG poisoning detection."""

    def __init__(self, sqlite_store):
        """
        Initialize metrics tracker.

        Args:
            sqlite_store: SQLiteStore instance for querying quarantine data
        """
        self.sqlite_store = sqlite_store
        self._last_update = None
        self._cached_metrics = {}

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get current metrics for quarantine activity.

        Returns:
            {
                "quarantine_count_total": int,
                "quarantine_count_24h": int,
                "quarantine_confidence_avg": float,
                "quarantine_confidence_p50": float,
                "quarantine_confidence_p95": float,
                "quarantine_high_confidence_pct": float,
                "detector_method_breakdown": {method: count},
                "quarantine_reason_top5": {reason: count},
            }
        """
        if not self.sqlite_store:
            return {}

        try:
            stats = self.sqlite_store.get_quarantine_stats()

            total = stats.get("total", 0)
            high = stats.get("high_confidence", 0)

            return {
                "quarantine_count_total": total,
                "quarantine_count_24h": stats.get("last_24h", 0),
                "quarantine_confidence_avg": stats.get("avg_confidence", 0.0),
                "quarantine_confidence_p50": stats.get("confidence_p50", 0.0),
                "quarantine_confidence_p95": stats.get("confidence_p95", 0.0),
                "quarantine_high_confidence_pct": (high / total * 100) if total > 0 else 0.0,
                "detector_method_breakdown": stats.get("methods", {}),
                "quarantine_reason_top5": stats.get("reasons", {}),
            }
        except Exception as e:
            logger.error("Failed to get poisoning metrics: %s", e)
            return {}

    def get_prometheus_format(self) -> str:
        """
        Get metrics in Prometheus text format.

        Returns:
            Prometheus-compatible metric text
        """
        metrics = self.get_metrics()

        lines = [
            "# HELP quarantine_count_total Total number of quarantined embeddings",
            "# TYPE quarantine_count_total gauge",
            f"quarantine_count_total {metrics.get('quarantine_count_total', 0)}",
            "",
            "# HELP quarantine_count_24h Quarantined embeddings in last 24 hours",
            "# TYPE quarantine_count_24h gauge",
            f"quarantine_count_24h {metrics.get('quarantine_count_24h', 0)}",
            "",
            "# HELP quarantine_confidence_avg Average detection confidence",
            "# TYPE quarantine_confidence_avg gauge",
            f"quarantine_confidence_avg {metrics.get('quarantine_confidence_avg', 0.0):.4f}",
            "",
            "# HELP quarantine_confidence_p50 Median detection confidence",
            "# TYPE quarantine_confidence_p50 gauge",
            f"quarantine_confidence_p50 {metrics.get('quarantine_confidence_p50', 0.0):.4f}",
            "",
            "# HELP quarantine_confidence_p95 95th percentile detection confidence",
            "# TYPE quarantine_confidence_p95 gauge",
            f"quarantine_confidence_p95 {metrics.get('quarantine_confidence_p95', 0.0):.4f}",
            "",
            "# HELP quarantine_high_confidence_pct Percentage with >0.8 confidence",
            "# TYPE quarantine_high_confidence_pct gauge",
            f"quarantine_high_confidence_pct {metrics.get('quarantine_high_confidence_pct', 0.0):.2f}",
            "",
        ]

        # Add method breakdown
        methods = metrics.get("detector_method_breakdown", {})
        if methods:
            lines.append("# HELP quarantine_detector_method_count Count by detection method")
            lines.append("# TYPE quarantine_detector_method_count gauge")
            for method, count in methods.items():
                lines.append(f'quarantine_detector_method_count{{method="{method}"}} {count}')
            lines.append("")

        # Add top reasons
        reasons = metrics.get("quarantine_reason_top5", {})
        if reasons:
            lines.append("# HELP quarantine_reason_count Count by reason (top 5)")
            lines.append("# TYPE quarantine_reason_count gauge")
            for reason, count in list(reasons.items())[:5]:
                # Sanitize reason for Prometheus label
                safe_reason = reason.replace('"', '\\"')[:50]
                lines.append(f'quarantine_reason_count{{reason="{safe_reason}"}} {count}')
            lines.append("")

        return "\n".join(lines)

    def get_json_metrics(self) -> Dict[str, Any]:
        """
        Get metrics as JSON-serializable dict.

        Returns:
            {
                "timestamp": ISO string,
                "metrics": {...},
            }
        """
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": self.get_metrics(),
        }
