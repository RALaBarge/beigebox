"""
BlueTruth tool — Bluetooth diagnostic & device simulation.

Enables agents to:
  - Inject mock Bluetooth events into bluTruth collector
  - Query bluTruth's SQLite for events, correlations, rule matches
  - Simulate device lifecycles (connect/disconnect/rssi/encryption changes)
  - Trigger pattern rules and validate detections
  - Generate diagnostic summaries for reasoning

Requires bluTruth to be running (e.g., `sudo blutruth serve`).
Communicates via REST API or direct SQLite access (if available).
"""

import json
import logging
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BlueTruthTool:
    """Bluetooth diagnostic and mock device simulation tool."""

    description = (
        "Bluetooth diagnostic and device simulation tool. Commands: "
        "inject_event (mock HCI/DBUS event), simulate_device (lifecycle: connect/disconnect/rssi), "
        "query_events (search by device/source/severity), query_correlations (linked events), "
        "list_devices (all discovered), rule_status (pattern detections), summary (diagnostics). "
        "Examples: {\"tool\": \"bluetruth\", \"input\": \"simulate_device action=connect device=AA:BB:CC:DD:EE:FF\"}"
    )

    def __init__(self, db_path: Optional[str] = None, api_url: Optional[str] = None):
        """
        Initialize BlueTruth tool.

        Args:
            db_path: Path to bluTruth SQLite DB (default: ~/.blutruth/events.db)
            api_url: REST API endpoint (default: http://localhost:8484 — bluTruth web server)
        """
        self.db_path = Path(db_path or Path.home() / ".blutruth" / "events.db")
        self.api_url = api_url or "http://localhost:8484"
        self._next_event_id = 1
        logger.info(f"BlueTruthTool initialized (db={self.db_path}, api={self.api_url})")

    def run(self, input_text: str) -> str:
        """
        Parse command from agent and execute.

        Format: "command arg1=value1 arg2=value2"
        Examples:
          - "simulate_device action=connect device=AA:BB:CC:DD:EE:FF"
          - "inject_event source=HCI event_type=CONNECT device=AA:BB:CC:DD:EE:FF"
          - "query_events device=AA:BB:CC:DD:EE:FF severity=WARN"
          - "list_devices"
          - "rule_status"
          - "summary"
        """
        try:
            parts = input_text.strip().split()
            if not parts:
                return json.dumps({"error": "Empty command"})

            command = parts[0]
            kwargs = {}
            for part in parts[1:]:
                if "=" in part:
                    key, value = part.split("=", 1)
                    kwargs[key] = value

            if command == "simulate_device":
                return self._simulate_device(**kwargs)
            elif command == "inject_event":
                return self._inject_event(**kwargs)
            elif command == "query_events":
                return self._query_events(**kwargs)
            elif command == "query_correlations":
                return self._query_correlations(**kwargs)
            elif command == "list_devices":
                return self._list_devices(**kwargs)
            elif command == "rule_status":
                return self._rule_status(**kwargs)
            elif command == "summary":
                return self._summary(**kwargs)
            else:
                return json.dumps({"error": f"Unknown command: {command}"})

        except Exception as e:
            logger.exception(f"BlueTruthTool error: {e}")
            return json.dumps({"error": str(e)})

    def _simulate_device(
        self,
        action: str = "connect",
        device: str = "AA:BB:CC:DD:EE:FF",
        name: str = "Test Device",
        rssi: int = -50,
        encrypted: bool = False,
        **kwargs,
    ) -> str:
        """
        Simulate a Bluetooth device lifecycle event.

        Actions:
          - connect: device discovers and connects
          - disconnect: device disconnects
          - rssi: RSSI signal strength changes
          - encrypt: encryption started
          - auth: authentication/pairing
        """
        try:
            actions = {
                "connect": self._device_connect,
                "disconnect": self._device_disconnect,
                "rssi": self._device_rssi,
                "encrypt": self._device_encrypt,
                "auth": self._device_auth,
            }

            if action not in actions:
                return json.dumps(
                    {"error": f"Unknown action: {action}. Use one of: {list(actions.keys())}"}
                )

            result = actions[action](device=device, name=name, rssi=int(rssi), encrypted=encrypted, **kwargs)
            return json.dumps(result)

        except Exception as e:
            logger.exception(f"simulate_device failed: {e}")
            return json.dumps({"error": str(e)})

    def _device_connect(self, device: str, name: str, **kwargs) -> dict:
        """Inject HCI_CONNECT_COMPLETE and DBUS device.Connected signal."""
        events = [
            {
                "source": "HCI",
                "event_type": "HCI_LE_CONNECTION_COMPLETE",
                "device": device,
                "device_name": name,
                "status": "success",
                "stage": "CONNECTION",
                "summary": f"Device {device} ({name}) connected",
            },
            {
                "source": "DBUS",
                "event_type": "device.Connected",
                "device": device,
                "device_name": name,
                "status": "success",
                "stage": "CONNECTION",
                "summary": f"D-Bus signal: {device} connected",
            },
        ]
        return self._inject_multiple_events(events, f"device_connect({device})")

    def _device_disconnect(self, device: str, **kwargs) -> dict:
        """Inject HCI_DISCONNECT_COMPLETE and DBUS device.Disconnected signal."""
        events = [
            {
                "source": "HCI",
                "event_type": "HCI_DISCONNECT_COMPLETE",
                "device": device,
                "reason": "local_user_termination",
                "status": "success",
                "stage": "TEARDOWN",
                "summary": f"Device {device} disconnected",
            },
            {
                "source": "DBUS",
                "event_type": "device.Disconnected",
                "device": device,
                "status": "success",
                "stage": "TEARDOWN",
                "summary": f"D-Bus signal: {device} disconnected",
            },
        ]
        return self._inject_multiple_events(events, f"device_disconnect({device})")

    def _device_rssi(self, device: str, rssi: int = -50, **kwargs) -> dict:
        """Inject HCI_LE_READ_REMOTE_USED_FEATURES with RSSI delta."""
        events = [
            {
                "source": "HCI",
                "event_type": "HCI_LE_RSSI_UPDATE",
                "device": device,
                "rssi": rssi,
                "stage": "DATA",
                "summary": f"RSSI for {device} changed to {rssi} dBm",
            },
        ]
        return self._inject_multiple_events(events, f"device_rssi({device}, {rssi})")

    def _device_encrypt(self, device: str, **kwargs) -> dict:
        """Inject HCI_ENCRYPTION_CHANGE event."""
        events = [
            {
                "source": "HCI",
                "event_type": "HCI_ENCRYPTION_CHANGE",
                "device": device,
                "encryption_enabled": True,
                "stage": "HANDSHAKE",
                "summary": f"Encryption enabled for {device}",
            },
        ]
        return self._inject_multiple_events(events, f"device_encrypt({device})")

    def _device_auth(self, device: str, **kwargs) -> dict:
        """Inject authentication/pairing sequence."""
        events = [
            {
                "source": "HCI",
                "event_type": "HCI_PIN_CODE_REQUEST",
                "device": device,
                "stage": "HANDSHAKE",
                "summary": f"PIN code requested for {device}",
            },
            {
                "source": "HCI",
                "event_type": "HCI_LINK_KEY_NOTIFICATION",
                "device": device,
                "stage": "HANDSHAKE",
                "summary": f"Link key established for {device}",
            },
        ]
        return self._inject_multiple_events(events, f"device_auth({device})")

    def _inject_multiple_events(self, events: list[dict], operation: str) -> dict:
        """Inject a sequence of events, return summary."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            inserted = []
            for evt in events:
                event_id = self._insert_event(cur, evt)
                inserted.append({**evt, "id": event_id})

            conn.commit()
            conn.close()

            return {
                "operation": operation,
                "status": "success",
                "events_injected": len(inserted),
                "events": inserted,
            }
        except Exception as e:
            logger.exception(f"_inject_multiple_events failed: {e}")
            return {"operation": operation, "status": "error", "error": str(e)}

    def _insert_event(self, cur: sqlite3.Cursor, evt: dict) -> int:
        """Insert event into SQLite, return row ID."""
        from datetime import datetime, timezone
        ts_mono_us = int(time.monotonic() * 1_000_000)
        ts_wall = datetime.now(timezone.utc).isoformat()
        source = evt.get("source", "MANUAL")
        event_type = evt.get("event_type", "UNKNOWN")
        device = evt.get("device", "")
        severity = evt.get("severity", "INFO")
        stage = evt.get("stage", "DATA")
        summary = evt.get("summary", "")

        try:
            cur.execute(
                """
                INSERT INTO events
                (ts_mono_us, ts_wall, source, event_type, device_addr, severity, stage, summary, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts_mono_us, ts_wall, source, event_type, device, severity, stage, summary, json.dumps(evt)),
            )
            return cur.lastrowid
        except sqlite3.OperationalError as e:
            # Table may not exist yet, return mock ID
            logger.warning(f"Could not insert into events table: {e}")
            self._next_event_id += 1
            return self._next_event_id

    def _inject_event(
        self,
        source: str = "HCI",
        event_type: str = "UNKNOWN",
        device: str = "AA:BB:CC:DD:EE:FF",
        **kwargs,
    ) -> str:
        """Inject a raw Bluetooth event."""
        try:
            event = {
                "source": source,
                "event_type": event_type,
                "device": device,
                "severity": kwargs.get("severity", "INFO"),
                "stage": kwargs.get("stage", "DATA"),
                "summary": kwargs.get("summary", f"{source}: {event_type}"),
                **kwargs,
            }
            result = self._inject_multiple_events([event], f"inject_event({source}, {event_type})")
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _query_events(
        self, device: Optional[str] = None, source: Optional[str] = None, severity: Optional[str] = None, limit: int = 50, **kwargs
    ) -> str:
        """Query events from bluTruth database."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            query = "SELECT * FROM events WHERE 1=1"
            params = []

            if device:
                query += " AND device_addr = ?"
                params.append(device)
            if source:
                query += " AND source = ?"
                params.append(source)
            if severity:
                query += " AND severity = ?"
                params.append(severity)

            query += f" ORDER BY ts_mono_us DESC LIMIT {limit}"

            cur.execute(query, params)
            rows = cur.fetchall()
            conn.close()

            events = [dict(row) for row in rows]
            return json.dumps(
                {
                    "status": "success",
                    "count": len(events),
                    "events": events,
                    "filters": {"device": device, "source": source, "severity": severity},
                }
            )

        except Exception as e:
            logger.exception(f"query_events failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _query_correlations(self, group_id: Optional[str] = None, device: Optional[str] = None, limit: int = 20, **kwargs) -> str:
        """Query correlated event groups."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get event groups
            query = "SELECT DISTINCT group_id FROM events WHERE group_id IS NOT NULL"
            params = []

            if device:
                query += " AND device_addr = ?"
                params.append(device)

            query += f" LIMIT {limit}"

            cur.execute(query, params)
            group_ids = [row["group_id"] for row in cur.fetchall()]

            # Fetch events in each group
            correlations = []
            for gid in group_ids:
                cur.execute(
                    "SELECT * FROM events WHERE group_id = ? ORDER BY ts_mono_us",
                    (gid,),
                )
                events = [dict(row) for row in cur.fetchall()]
                correlations.append({"group_id": gid, "event_count": len(events), "events": events})

            conn.close()

            return json.dumps(
                {
                    "status": "success",
                    "correlation_count": len(correlations),
                    "correlations": correlations,
                }
            )

        except Exception as e:
            logger.exception(f"query_correlations failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _list_devices(self, **kwargs) -> str:
        """List all discovered Bluetooth devices."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute(
                """
                SELECT DISTINCT device_addr,
                       COUNT(*) as event_count,
                       MAX(ts_mono_us) as last_seen
                FROM events
                WHERE device_addr IS NOT NULL AND device_addr != ''
                GROUP BY device_addr
                ORDER BY last_seen DESC
                """
            )

            devices = []
            for row in cur.fetchall():
                devices.append(
                    {
                        "address": row["device_addr"],
                        "event_count": row["event_count"],
                        "last_seen_us": row["last_seen"],
                    }
                )

            conn.close()

            return json.dumps(
                {
                    "status": "success",
                    "device_count": len(devices),
                    "devices": devices,
                }
            )

        except Exception as e:
            logger.exception(f"list_devices failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _rule_status(self, **kwargs) -> str:
        """Query pattern rule detections and matches."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # bluTruth stores rule matches in events table with a specific tag
            cur.execute(
                """
                SELECT DISTINCT event_type, severity, COUNT(*) as count
                FROM events
                WHERE tags_json LIKE '%rule%' OR event_type LIKE '%RULE%'
                GROUP BY event_type, severity
                ORDER BY count DESC
                """
            )

            rules = []
            for row in cur.fetchall():
                rules.append(
                    {
                        "rule": row["event_type"],
                        "severity": row["severity"],
                        "match_count": row["count"],
                    }
                )

            conn.close()

            return json.dumps(
                {
                    "status": "success",
                    "rule_count": len(rules),
                    "rules": rules,
                }
            )

        except Exception as e:
            logger.exception(f"rule_status failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})

    def _summary(self, **kwargs) -> str:
        """Generate overall diagnostic summary."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Event counts by source and severity
            cur.execute(
                """
                SELECT source, severity, COUNT(*) as count
                FROM events
                GROUP BY source, severity
                ORDER BY source, severity
                """
            )
            event_breakdown = [dict(row) for row in cur.fetchall()]

            # Device counts
            cur.execute("SELECT COUNT(DISTINCT device_addr) as device_count FROM events")
            device_count = cur.fetchone()["device_count"] or 0

            # Recent errors
            cur.execute(
                """
                SELECT * FROM events
                WHERE severity IN ('ERROR', 'WARN')
                ORDER BY ts_mono_us DESC LIMIT 10
                """
            )
            recent_issues = [dict(row) for row in cur.fetchall()]

            conn.close()

            return json.dumps(
                {
                    "status": "success",
                    "summary": {
                        "total_devices": device_count,
                        "event_breakdown": event_breakdown,
                        "recent_errors": len(recent_issues),
                        "recent_issues": recent_issues,
                    },
                }
            )

        except Exception as e:
            logger.exception(f"summary failed: {e}")
            return json.dumps({"status": "error", "error": str(e)})
