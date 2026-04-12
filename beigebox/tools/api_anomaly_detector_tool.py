"""
APIAnomalyDetectorTool — Operator-callable tool for API anomaly detection.

Exposes the APIAnomalyDetector via the standard tool interface so the
Operator (or automated alerts) can:
  - Analyze a live session for anomalies
  - Query historical anomaly events
  - Get a full security report across all sessions
  - View/manage baselines

Commands:
  analyze ip=<ip> [sensitivity=low|medium|high] [time_window=5]
  report [sensitivity=low|medium|high]
  history [ip=<ip>] [limit=50]
  baselines
  stats ip=<ip>

Output: JSON with anomalies, risk_score, recommended_action.
"""

import json
import logging

logger = logging.getLogger(__name__)


class APIAnomalyDetectorTool:
    """
    Tool for querying the API anomaly detection system.

    Wraps APIAnomalyDetector to provide an agent-callable interface.
    """

    description = (
        "API anomaly detection tool: detect suspicious API usage patterns "
        "(request rate spikes, error rate changes, model switching, payload anomalies). "
        "Commands: "
        "analyze ip=<ip> [sensitivity=low|medium|high] [time_window=5] — check IP for anomalies; "
        "report — full security report across all sessions; "
        "history [ip=<ip>] [limit=50] — view past anomaly events; "
        "baselines — show all stored baselines; "
        "stats ip=<ip> — session statistics for an IP. "
        "Output: JSON with anomalies, risk_score, recommended_action."
    )

    def __init__(self, detector=None):
        """
        Args:
            detector: APIAnomalyDetector instance (from Proxy or standalone)
        """
        self._detector = detector

    def set_detector(self, detector) -> None:
        """Late-bind the detector (useful when tool is registered before proxy init)."""
        self._detector = detector

    def run(self, input_text: str) -> str:
        """Parse command and dispatch."""
        if not self._detector:
            return json.dumps({"error": "Anomaly detector not initialized"})

        parts = input_text.strip().split()
        if not parts:
            return json.dumps({"error": "No command. Use: analyze, report, history, baselines, stats"})

        command = parts[0].lower()
        kwargs = self._parse_kwargs(parts[1:])

        try:
            if command == "analyze":
                return self._cmd_analyze(kwargs)
            elif command == "report":
                return self._cmd_report(kwargs)
            elif command == "history":
                return self._cmd_history(kwargs)
            elif command == "baselines":
                return self._cmd_baselines()
            elif command == "stats":
                return self._cmd_stats(kwargs)
            else:
                return json.dumps({"error": f"Unknown command: {command}. Use: analyze, report, history, baselines, stats"})
        except Exception as e:
            logger.error("APIAnomalyDetectorTool error: %s", e)
            return json.dumps({"error": str(e)})

    def _cmd_analyze(self, kwargs: dict) -> str:
        """Analyze an IP for anomalies."""
        ip = kwargs.get("ip", "")
        if not ip:
            return json.dumps({"error": "analyze requires ip=<address>"})

        time_window = int(kwargs.get("time_window", 0))
        result = self._detector.analyze(
            ip=ip,
            user_agent=kwargs.get("user_agent", ""),
            request_bytes=int(kwargs.get("request_bytes", 0)),
            time_window=time_window,
        )
        return json.dumps(result, default=str)

    def _cmd_report(self, kwargs: dict) -> str:
        """Generate full security report."""
        report = self._detector.get_anomaly_report()
        return json.dumps(report, default=str)

    def _cmd_history(self, kwargs: dict) -> str:
        """Get historical anomaly events."""
        ip = kwargs.get("ip", "")
        limit = int(kwargs.get("limit", 50))
        events = self._detector.get_historical_events(ip=ip, limit=limit)
        return json.dumps({"events": events, "count": len(events)}, default=str)

    def _cmd_baselines(self) -> str:
        """Show all baselines."""
        baselines = self._detector.get_all_baselines()
        return json.dumps({"baselines": baselines, "count": len(baselines)}, default=str)

    def _cmd_stats(self, kwargs: dict) -> str:
        """Get session stats for an IP."""
        ip = kwargs.get("ip", "")
        if not ip:
            return json.dumps({"error": "stats requires ip=<address>"})
        stats = self._detector.get_session_stats(ip)
        if not stats:
            return json.dumps({"error": f"No session data for {ip}"})
        return json.dumps(stats, default=str)

    @staticmethod
    def _parse_kwargs(parts: list[str]) -> dict:
        """Parse key=value pairs from command arguments."""
        kwargs = {}
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                kwargs[key.strip()] = value.strip()
        return kwargs
