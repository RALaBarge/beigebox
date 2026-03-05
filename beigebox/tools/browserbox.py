"""
BrowserBox tool — gives the operator agent access to browser APIs
(storage, fetch) via the local BrowserBox WebSocket relay.

Requires:
  - ws_relay.py running on localhost:9009
  - BrowserBox Chrome extension connected to the relay

Config (config.yaml):
  tools:
    browserbox:
      enabled: true
      ws_url: ws://localhost:9009
      timeout: 10
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class BrowserboxTool:
    description = (
        "Access browser APIs: DOM, storage, and authenticated fetch. "
        "Input must be JSON: {\"tool\": \"namespace.method\", \"input\": \"...\"}. "
        "Namespaces: "
        "dom (query/query_all/get_text/get_html/get_url/get_title/click/fill/scroll/wait_for/snapshot), "
        "storage (get/set/delete/list/get_cookie/list_cookies), "
        "fetch (get/post/head). "
        "dom tools operate on the active browser tab. "
        "fetch calls carry the browser's real session cookies. "
        "Start with dom.snapshot to orient yourself on a page. "
        "Example: {\"tool\": \"dom.snapshot\", \"input\": \"\"}"
    )

    def __init__(self, ws_url: str = "ws://localhost:9009", timeout: float = 10.0):
        self._ws_url = ws_url
        self._timeout = timeout

    def run(self, input_str: str) -> str:
        """Synchronous wrapper — runs the async call in a new event loop."""
        try:
            params = json.loads(input_str.strip())
        except json.JSONDecodeError:
            return "Error: input must be JSON {\"tool\": \"ns.method\", \"input\": \"...\"}"

        tool = params.get("tool", "")
        inp  = params.get("input", "")
        if not tool:
            return "Error: missing 'tool' field"

        try:
            return asyncio.run(self._call(tool, inp))
        except Exception as e:
            logger.error("browserbox call failed: %s", e)
            return f"Error: {e}"

    async def _call(self, tool: str, input_value: Any) -> str:
        try:
            import websockets  # type: ignore
        except ImportError:
            return "Error: websockets not installed — pip install websockets"

        call_id = str(uuid.uuid4())
        payload = json.dumps({"id": call_id, "tool": tool, "input": input_value})

        try:
            async with websockets.connect(
                self._ws_url,
                open_timeout=self._timeout,
                close_timeout=2,
            ) as ws:
                # Announce as agent role
                await ws.send(json.dumps({"role": "agent"}))
                # Send the tool call
                await ws.send(payload)
                # Wait for the matching response
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("id") != call_id:
                        continue
                    if "error" in msg:
                        return f"Error: {msg['error']}"
                    result = msg.get("result")
                    if result is None:
                        return "null"
                    return str(result)
        except OSError as e:
            return f"Error: could not connect to BrowserBox relay at {self._ws_url} — {e}"
        except TimeoutError:
            return f"Error: timed out waiting for response from BrowserBox"
