"""
BrowserBox tool — gives the operator agent access to browser APIs via the
local BrowserBox WebSocket relay.

Requires:
  - ws_relay.py running on localhost:9009
  - BrowserBox Chrome extension connected to the relay

Config (config.yaml):
  tools:
    browserbox:
      enabled: true
      ws_url: ws://localhost:9009
      timeout: 10
      workspace_in: ./workspace/in   # for pdf.extract saves
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BrowserboxTool:
    description = (
        "Access browser APIs via the active Chrome tab. "
        "Input must be JSON: {\"tool\": \"namespace.method\", \"input\": \"...\"}. "
        "Namespaces and methods:\n"
        "  dom     — snapshot, query, query_all, get_text, get_html, get_url, get_title, "
                     "click, fill, scroll, wait_for\n"
        "  tabs    — list, get_current, open, close, switch, screenshot\n"
        "  nav     — go, back, forward, reload\n"
        "  clip    — read, write\n"
        "  storage — get, set, delete, list, get_cookie, list_cookies\n"
        "  fetch   — get, post, head  (carries real session cookies)\n"
        "  network — start_capture, stop_capture, get_captured, clear\n"
        "  inject  — js, css, css_remove\n"
        "  pdf     — extract  (fetches current tab PDF → saves to workspace/in/)\n"
        "Start with dom.snapshot to orient on the active page. "
        "For SPA scraping: network.start_capture, interact, network.get_captured. "
        "Example: {\"tool\": \"dom.snapshot\", \"input\": \"\"}"
    )

    def __init__(
        self,
        ws_url: str = "ws://localhost:9009",
        timeout: float = 10.0,
        workspace_in: str | Path | None = None,
    ):
        self._ws_url = ws_url
        self._timeout = timeout
        self._workspace_in = Path(workspace_in) if workspace_in else None

    def run(self, input_str: str) -> str:
        try:
            params = json.loads(input_str.strip())
        except json.JSONDecodeError:
            return 'Error: input must be JSON {"tool": "ns.method", "input": "..."}'

        tool = params.get("tool", "")
        inp  = params.get("input", "")
        if not tool:
            return "Error: missing 'tool' field"

        inp_snippet = str(inp)[:80] + ("…" if len(str(inp)) > 80 else "")
        logger.info("browserbox: calling %s (relay=%s, input=%.80s)", tool, self._ws_url, inp_snippet)

        try:
            result = asyncio.run(self._call(tool, inp))
        except Exception as e:
            logger.error("browserbox: unexpected exception calling %s — %s: %s", tool, type(e).__name__, e)
            return f"Error: {e}"

        # Classify and log the outcome
        if result.startswith("Error: could not connect"):
            logger.warning("browserbox: relay unreachable at %s — is ws_relay.py running?", self._ws_url)
        elif result.startswith("Error: timed out"):
            logger.warning("browserbox: %s timed out after %.0fs", tool, self._timeout)
        elif "browser not connected" in result:
            logger.warning(
                "browserbox: relay is up but extension is not connected — "
                "open Chrome, load the BrowserBox extension, and check the popup"
            )
        elif result.startswith("Error:"):
            logger.warning("browserbox: %s returned error: %s", tool, result)
        else:
            snippet = result[:120] + ("…" if len(result) > 120 else "")
            logger.info("browserbox: %s OK → %s", tool, snippet)

        return result

    async def _call(self, tool: str, input_value: Any) -> str:
        try:
            import websockets  # type: ignore
        except ImportError:
            return "Error: websockets not installed — pip install websockets"

        call_id = str(uuid.uuid4())
        payload = json.dumps({"id": call_id, "tool": tool, "input": input_value})

        # Reserve at most 3s for the TCP handshake; the remaining budget is for the
        # actual tool round-trip (extension dispatches + response).
        connect_timeout = min(self._timeout * 0.3, 3.0)
        recv_deadline   = self._timeout - connect_timeout

        logger.debug("browserbox: connecting to %s (call_id=%s)", self._ws_url, call_id)
        try:
            async with websockets.connect(
                self._ws_url,
                open_timeout=connect_timeout,
                close_timeout=2,
            ) as ws:
                await ws.send(json.dumps({"role": "agent"}))
                await ws.send(payload)
                logger.debug("browserbox: sent %s, waiting for response (%.0fs budget)…",
                             tool, recv_deadline)

                # Read messages with a hard deadline — guards against the extension
                # being silently dead (SW killed but socket still open at OS level).
                end = asyncio.get_event_loop().time() + recv_deadline
                while True:
                    remaining = end - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        return "Error: timed out waiting for response from BrowserBox"
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except (asyncio.TimeoutError, TimeoutError):
                        return "Error: timed out waiting for response from BrowserBox"
                    msg = json.loads(raw)
                    if msg.get("id") != call_id:
                        continue
                    if "error" in msg:
                        logger.debug("browserbox: relay returned error for %s: %s", tool, msg["error"])
                        return f"Error: {msg['error']}"
                    result = msg.get("result")
                    if result is None:
                        return "null"
                    # Special handling: pdf.extract → save bytes to workspace/in/
                    if tool == "pdf.extract":
                        return self._save_pdf(result)
                    return str(result)
        except OSError as e:
            logger.debug("browserbox: OSError connecting to %s — %s", self._ws_url, e)
            return f"Error: could not connect to BrowserBox relay at {self._ws_url} — {e}"
        except (asyncio.TimeoutError, TimeoutError):
            logger.debug("browserbox: connection timed out to %s", self._ws_url)
            return "Error: timed out waiting for response from BrowserBox"

    def _save_pdf(self, result: str) -> str:
        """Decode base64 PDF from pdf.extract, save to workspace/in/, return instructions."""
        try:
            data = json.loads(result)
            filename  = data.get("filename", "document.pdf")
            bytes_b64 = data.get("bytes_b64", "")
            size      = data.get("size_bytes", 0)
            url       = data.get("url", "")
        except (json.JSONDecodeError, AttributeError) as e:
            return f"Error: unexpected pdf.extract response: {e}"

        if not bytes_b64:
            return "Error: pdf.extract returned empty bytes"

        if self._workspace_in:
            dest = self._workspace_in / filename
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(base64.b64decode(bytes_b64))
                return (
                    f"PDF saved to workspace/in/{filename} ({size:,} bytes, from {url}). "
                    f"Call pdf_reader with '{filename}' to extract content."
                )
            except Exception as e:
                logger.error("pdf save failed: %s", e)
                return f"Error saving PDF to workspace: {e}"
        else:
            # No workspace configured — return metadata only
            return (
                f"PDF fetched: {filename} ({size:,} bytes, from {url}). "
                f"No workspace configured — enable tools.pdf_reader and set workspace path "
                f"to save and read this file."
            )
