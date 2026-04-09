"""
Minimal async BrowserBox WebSocket client shared by tools that need to talk
to the BrowserBox relay (ws://localhost:9009 by default).

Wire protocol (matches browserbox/ws_relay.py):
    1. Client connects and sends: {"role": "agent"}
    2. Client sends: {"id": "<uuid>", "tool": "<ns>.<method>", "input": <value>}
    3. Server replies: {"id": "<uuid>", "result": <string>} or {"id", "error"}

Usage:
    client = BBClient()
    raw = await client.call("inject.aura_actions", {})
    data = json.loads(raw) if raw else None
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

_DEFAULT_WS_URL  = "ws://localhost:9009"
_DEFAULT_TIMEOUT = 15.0


class BBClient:
    def __init__(self, ws_url: str = _DEFAULT_WS_URL, timeout: float = _DEFAULT_TIMEOUT):
        self._url     = ws_url
        self._timeout = timeout

    async def call(self, tool: str, input_value: Any) -> str:
        """
        Dial the relay, send one tool call, return the raw result string.
        Raises RuntimeError on relay/tool error, TimeoutError on deadline miss.
        """
        try:
            import websockets  # type: ignore[import]
        except ImportError as e:
            raise RuntimeError("websockets not installed — pip install websockets") from e

        call_id = str(uuid.uuid4())
        payload = json.dumps({"id": call_id, "tool": tool, "input": input_value})

        connect_timeout = min(self._timeout * 0.3, 5.0)
        recv_deadline   = max(self._timeout - connect_timeout, 1.0)

        async with websockets.connect(
            self._url,
            open_timeout=connect_timeout,
            close_timeout=2,
        ) as ws:
            await ws.send(json.dumps({"role": "agent"}))
            await ws.send(payload)
            end = asyncio.get_event_loop().time() + recv_deadline
            while True:
                remaining = end - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for {tool}")
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                if msg.get("id") != call_id:
                    continue
                if "error" in msg:
                    raise RuntimeError(f"BrowserBox error: {msg['error']}")
                return msg.get("result", "") or ""

    async def aura(self, descriptor: str, params: dict | None = None) -> dict:
        """
        Invoke a Salesforce Lightning Aura action via `inject.aura` and return
        the first action's `returnValue`. Handles the `while(1);` prefix and
        aura error envelopes, raising RuntimeError on failure.
        """
        raw = await self.call("inject.aura", {
            "descriptor": descriptor,
            "params":     params or {},
        })
        if not raw:
            raise RuntimeError("inject.aura returned null")
        outer = json.loads(raw)
        body = outer.get("body", "") or ""
        if body.startswith("while(1);"):
            body = body[len("while(1);"):].lstrip("\n")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"non-JSON aura response: {body[:300]}") from e
        actions = parsed.get("actions", []) if isinstance(parsed, dict) else []
        if not actions:
            raise RuntimeError(f"aura response had no actions: {body[:300]}")
        a = actions[0]
        if a.get("state") != "SUCCESS":
            raise RuntimeError(
                f"aura action failed: state={a.get('state')} err={a.get('error')}"
            )
        return a.get("returnValue") or {}
