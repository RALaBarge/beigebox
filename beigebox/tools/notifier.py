"""
Webhook notifier — sends tool invocation data to an external listener.

Point this at netcat, a webhook endpoint, or any TCP listener to watch
BeigeBox tools fire in real time.

Usage:
    # Terminal 1: start a listener
    nc -lk 9999

    # config.yaml:
    tools:
      webhook_url: "http://localhost:9999"

Every tool invocation sends a JSON payload to the webhook URL.
If the endpoint is down, the notification is logged and skipped — never blocks.
"""

import json
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


class ToolNotifier:
    """Sends tool invocation events to a webhook/netcat listener."""

    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url.rstrip("/")
        self.enabled = bool(webhook_url)
        if self.enabled:
            logger.info("ToolNotifier enabled: %s", self.webhook_url)

    def notify(self, tool_name: str, input_text: str, output: str, duration_ms: float = 0):
        """Fire-and-forget notification to the webhook."""
        if not self.enabled:
            return

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "input": input_text[:500],
            "output": output[:1000],
            "output_len": len(output),
            "duration_ms": round(duration_ms, 1),
        }

        try:
            # For raw TCP (netcat), just send the JSON line
            if not self.webhook_url.startswith("http"):
                self._send_tcp(payload)
            else:
                self._send_http(payload)
        except Exception as e:
            logger.debug("Webhook notify failed (non-fatal): %s", e)

    def _send_http(self, payload: dict):
        """Send via HTTP POST."""
        try:
            httpx.post(
                self.webhook_url,
                json=payload,
                timeout=2.0,
            )
        except Exception:
            pass

    def _send_tcp(self, payload: dict):
        """Send raw JSON line via TCP (for netcat listeners)."""
        import socket

        # Parse host:port from URL like "tcp://localhost:9999"
        addr = self.webhook_url.replace("tcp://", "")
        if ":" in addr:
            host, port = addr.rsplit(":", 1)
            port = int(port)
        else:
            host = addr
            port = 9999

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect((host, port))
                sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        except Exception:
            pass
