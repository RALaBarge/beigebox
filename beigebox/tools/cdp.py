"""
CDP (Chrome DevTools Protocol) tool for the operator agent.

Connects to a running Chrome/Chromium instance with remote debugging enabled
via WebSocket (CDP). Provides navigate, screenshot, dom_snapshot, click, type,
and scroll operations.

Start Chrome with:
    google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/beigebox-cdp

Or via Docker Compose:
    services:
      chrome:
        image: chromium-headless
        command: --remote-debugging-port=9222 --headless

Config (config.yaml):
    tools:
      cdp:
        enabled: false
        ws_url: ws://localhost:9222
        timeout: 10

Operator call format:
    {"tool": "cdp.navigate",      "input": "https://example.com"}
    {"tool": "cdp.screenshot",    "input": ""}
    {"tool": "cdp.dom_snapshot",  "input": ""}
    {"tool": "cdp.click",         "input": "#submit-button"}
    {"tool": "cdp.type",          "input": {"selector": "#search", "text": "hello"}}
    {"tool": "cdp.scroll",        "input": {"x": 0, "y": 500}}
    {"tool": "cdp.eval",          "input": "document.title"}
    {"tool": "cdp.list_tabs",     "input": ""}

    Phase 2 (Network, Performance, Storage):
    {"tool": "cdp.network",       "input": {"action": "capture", "limit": 50}}
    {"tool": "cdp.console",       "input": ""}
    {"tool": "cdp.performance",   "input": ""}
    {"tool": "cdp.cookies",       "input": {"action": "list"}}
    {"tool": "cdp.storage",       "input": {"action": "list"}}

    Phase 3 (IndexedDB, Service Workers, Cache, Throttling):
    {"tool": "cdp.indexeddb",     "input": {"action": "list"}}
    {"tool": "cdp.service_worker","input": {"action": "list"}}
    {"tool": "cdp.cache",         "input": {"action": "list"}}
    {"tool": "cdp.throttle",      "input": {"action": "set", "latency": 100, "download": 1000, "upload": 500}}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────
_DEFAULT_WS_URL = "ws://localhost:9222"
_DEFAULT_TIMEOUT = 10.0
# HTTP endpoint for CDP target discovery (tab list)
_CDP_HTTP_BASE = "http://localhost:9222"
# Maximum chars returned to the operator context; full output goes to blob store
_MAX_CONTEXT_CHARS = 6000


class CDPClient:
    """
    Thin async CDP client that wraps a WebSocket connection to one Chrome tab.

    Handles:
    - Per-request message IDs
    - Automatic reconnection on stale/closed socket
    - Hard timeout per command (never blocks forever)
    """

    def __init__(self, ws_url: str, timeout: float = _DEFAULT_TIMEOUT,
                 session_id: str | None = None) -> None:
        self._ws_url = ws_url
        self._timeout = timeout
        self._ws = None
        self._cmd_id = 0
        # When set, all commands are routed through this CDP session (flattened sessions mode).
        # Used when connecting via the browser-level WS URL with Target.attachToTarget.
        self._session_id = session_id

    async def connect(self) -> None:
        """Open (or re-open) the WebSocket to the CDP target."""
        try:
            import websockets  # type: ignore[import]
        except ImportError:
            raise RuntimeError("websockets not installed — pip install websockets")

        connect_timeout = min(self._timeout * 0.3, 3.0)
        self._ws = await asyncio.wait_for(
            websockets.connect(
                self._ws_url,
                open_timeout=connect_timeout,
                close_timeout=2,
                ping_interval=None,  # disable auto-ping to keep CDP sessions clean
            ),
            timeout=connect_timeout + 1,
        )
        logger.debug("CDPClient connected to %s (session=%s)", self._ws_url, self._session_id)

    async def close(self) -> None:
        """Close the WebSocket if open."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _is_closed(self) -> bool:
        """Return True if the WebSocket is closed/closing. Handles websockets v10–v16 API changes."""
        if self._ws is None:
            return True
        # websockets ≤13: .closed property
        if hasattr(self._ws, "closed"):
            return self._ws.closed  # type: ignore[attr-defined]
        # websockets ≥14: .state enum (OPEN=1, CLOSING=2, CLOSED=3)
        if hasattr(self._ws, "state"):
            import websockets.connection as _wsc
            return self._ws.state != _wsc.State.OPEN
        # Fallback: assume open
        return False

    async def send(self, method: str, params: dict | None = None) -> dict:
        """
        Send a CDP command and await the response.

        Reconnects automatically if the socket is closed/None.
        Raises TimeoutError if the response doesn't arrive within self._timeout.
        When self._session_id is set, uses flattened CDP sessions (Target.attachToTarget mode).
        """
        if self._ws is None or self._is_closed():
            logger.debug("CDPClient: (re)connecting to %s", self._ws_url)
            await self.connect()

        self._cmd_id += 1
        msg_id = self._cmd_id
        payload: dict = {"id": msg_id, "method": method, "params": params or {}}
        if self._session_id:
            payload["sessionId"] = self._session_id

        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:
            # Socket died mid-send — reconnect once and retry
            logger.debug("CDPClient: send failed (%s), reconnecting", exc)
            await self.close()
            await self.connect()
            self._cmd_id += 1
            msg_id = self._cmd_id
            payload["id"] = msg_id
            await self._ws.send(json.dumps(payload))

        # Read until we get the response for our command id (and matching sessionId if set)
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP command '{method}' timed out after {self._timeout}s")
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=min(remaining, 2.0))
            except (asyncio.TimeoutError, TimeoutError):
                remaining2 = deadline - time.monotonic()
                if remaining2 <= 0:
                    raise TimeoutError(f"CDP command '{method}' timed out after {self._timeout}s")
                continue

            msg = json.loads(raw)

            # CDP events (method key present, no id) — skip
            if "method" in msg and "id" not in msg:
                continue

            if msg.get("id") == msg_id:
                # In session mode, also require sessionId to match
                if self._session_id and msg.get("sessionId") != self._session_id:
                    continue
                if "error" in msg:
                    raise RuntimeError(f"CDP error: {msg['error']}")
                return msg.get("result", {})
            # Response for a different command — discard (stale)


class CDPTool:
    """
    Operator-callable CDP tool. Registered in the tool registry as 'cdp'.

    Input format (JSON string):
        {"tool": "cdp.<method>", "input": <value>}

    Methods:
        cdp.navigate     — Navigate to URL
        cdp.screenshot   — Take screenshot (base64 PNG returned as summary + saved)
        cdp.dom_snapshot — Accessibility tree / DOM snapshot
        cdp.click        — Click by CSS selector
        cdp.type         — Type text into a selector
        cdp.scroll       — Scroll to (x, y)
        cdp.eval         — Evaluate JS expression (returns value as string)
        cdp.list_tabs    — List open tabs via HTTP /json/list
    """

    capture_tool_io: bool = True
    max_context_chars: int = _MAX_CONTEXT_CHARS

    description = (
        "Interact with a Chrome browser via Chrome DevTools Protocol (CDP).\n"
        "REQUIRED input format — always a JSON object with exactly these two keys:\n"
        '  {"tool": "cdp.<method>", "input": <value>}\n'
        "\n"
        "PHASE 1 (Navigation & Interaction):\n"
        "  cdp.navigate     — Navigate to URL:  {\"tool\": \"cdp.navigate\", \"input\": \"https://example.com\"}\n"
        "  cdp.screenshot   — Take screenshot:  {\"tool\": \"cdp.screenshot\", \"input\": \"\"}\n"
        "  cdp.dom_snapshot — DOM/a11y snapshot: {\"tool\": \"cdp.dom_snapshot\", \"input\": \"\"}\n"
        '  cdp.click        — Click selector:   {"tool": "cdp.click", "input": "#submit"}\n'
        '  cdp.type         — Type into field:  {"tool": "cdp.type", "input": {"selector": "#q", "text": "hello"}}\n'
        '  cdp.scroll       — Scroll page:      {"tool": "cdp.scroll", "input": {"x": 0, "y": 500}}\n'
        '  cdp.eval         — Evaluate JS:      {"tool": "cdp.eval", "input": "document.title"}\n'
        "  cdp.list_tabs    — List open tabs:   {\"tool\": \"cdp.list_tabs\", \"input\": \"\"}\n"
        "\n"
        "PHASE 2 (Network, Performance, Storage):\n"
        '  cdp.network      — Capture requests: {"tool": "cdp.network", "input": {"action": "capture", "limit": 50}}\n'
        '  cdp.console      — Get console logs: {"tool": "cdp.console", "input": ""}\n'
        '  cdp.performance  — Core Web Vitals:  {"tool": "cdp.performance", "input": ""}\n'
        '  cdp.cookies      — List/delete:      {"tool": "cdp.cookies", "input": {"action": "list"}}\n'
        '  cdp.storage      — LocalStorage/etc: {"tool": "cdp.storage", "input": {"action": "list"}}\n'
        "\n"
        "PHASE 3 (IndexedDB, Service Workers, Cache, Throttling):\n"
        '  cdp.indexeddb    — List DBs/stores: {"tool": "cdp.indexeddb", "input": {"action": "list"}}\n'
        '  cdp.service_worker — List/unregister: {"tool": "cdp.service_worker", "input": {"action": "list"}}\n'
        '  cdp.cache        — List cache names: {"tool": "cdp.cache", "input": {"action": "list"}}\n'
        '  cdp.throttle     — Network throttle: {"tool": "cdp.throttle", "input": {"action": "set", "latency": 100, "download": 1000, "upload": 500}}\n'
        "\n"
        "MIMIC MODE (Browser fingerprinting):\n"
        '  cdp.mimic_activate   — Link host cookies + inject headers: {"tool": "cdp.mimic_activate", "input": ""}\n'
        '  cdp.mimic_deactivate — Remove links, reset headers:      {"tool": "cdp.mimic_deactivate", "input": ""}\n'
        "\n"
        "Requires Chrome/Chromium running with --remote-debugging-port=9222.\n"
        "Errors are returned as strings (never crash — check result for 'Error:' prefix)."
    )

    def __init__(
        self,
        ws_url: str = _DEFAULT_WS_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        import threading
        self._ws_url = ws_url.rstrip("/")
        self._timeout = timeout
        # Derive the HTTP base URL for tab discovery from the WebSocket URL
        http_base = ws_url.replace("ws://", "http://").replace("wss://", "https://")
        from urllib.parse import urlparse
        parsed = urlparse(http_base)
        self._http_base = f"{parsed.scheme}://{parsed.netloc}"
        # CDP client (lives in _loop; never touched from other threads directly)
        self._client: CDPClient | None = None
        # Mimic mode state — tracks symlinks and headers set
        self._mimic_active: bool = False
        self._mimic_symlinks: list[str] = []  # Paths created during activate
        # Persistent background event loop so the WebSocket session survives across calls.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="cdp-loop"
        )
        self._loop_thread.start()

    # ------------------------------------------------------------------
    # Sync entry point for the tool registry
    # ------------------------------------------------------------------

    def run(self, input_str: str) -> str:
        """Parse input JSON and dispatch to the appropriate CDP method."""
        try:
            params = json.loads(input_str.strip())
        except json.JSONDecodeError:
            return 'Error: input must be JSON {"tool": "cdp.<method>", "input": "..."}'

        tool = params.get("tool", "")
        inp = params.get("input", "")

        if not tool:
            return "Error: missing 'tool' field"

        method = tool.removeprefix("cdp.") if tool.startswith("cdp.") else tool

        inp_repr = json.dumps(inp) if not isinstance(inp, str) else inp
        logger.info("cdp: %s (input=%.80s)", method, inp_repr[:80])

        try:
            future = asyncio.run_coroutine_threadsafe(self._dispatch(method, inp), self._loop)
            return future.result(timeout=self._timeout + 5)
        except Exception as exc:
            logger.error("cdp: unexpected error in %s: %s", method, exc)
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # Async dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, method: str, inp: Any) -> str:
        # Phase 1
        if method == "list_tabs":
            return await self._list_tabs()
        elif method == "navigate":
            return await self._navigate(str(inp))
        elif method == "screenshot":
            return await self._screenshot()
        elif method == "dom_snapshot":
            return await self._dom_snapshot()
        elif method == "click":
            return await self._click(str(inp))
        elif method == "type":
            return await self._type(inp)
        elif method == "scroll":
            return await self._scroll(inp)
        elif method == "eval":
            return await self._eval(str(inp))
        # Phase 2
        elif method == "network":
            return await self._network(inp)
        elif method == "console":
            return await self._console()
        elif method == "performance":
            return await self._performance()
        elif method == "cookies":
            return await self._cookies(inp)
        elif method == "storage":
            return await self._storage(inp)
        # Phase 3
        elif method == "indexeddb":
            return await self._indexeddb(inp)
        elif method == "service_worker":
            return await self._service_worker(inp)
        elif method == "cache":
            return await self._cache(inp)
        elif method == "throttle":
            return await self._throttle(inp)
        # Mimic mode
        elif method == "mimic_activate":
            return await self._mimic_activate()
        elif method == "mimic_deactivate":
            return await self._mimic_deactivate()
        else:
            return f"Error: unknown CDP method '{method}'"

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    async def _list_tabs(self) -> str:
        """Fetch open tabs from Chrome's /json/list endpoint (HTTP, not CDP WS)."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._http_base}/json/list")
                resp.raise_for_status()
                tabs = resp.json()
        except httpx.ConnectError:
            return (
                f"Error: could not connect to Chrome at {self._http_base}. "
                "Start Chrome with --remote-debugging-port=9222"
            )
        except Exception as exc:
            return f"Error listing tabs: {exc}"

        if not tabs:
            return "No open tabs found."

        lines = ["Open tabs:"]
        for tab in tabs:
            # /json/list uses "id" on standard Chrome but "targetId" on Puppeteer/browserless
            tab_id = (tab.get("id") or tab.get("targetId") or "?")
            tab_id = tab_id[-12:] if len(tab_id) > 12 else tab_id
            tab_type = tab.get("type", "?")
            tab_url = tab.get("url", "?")[:80]
            title = tab.get("title", "")[:60]
            lines.append(f"  [{tab_id}] ({tab_type}) {title!r} — {tab_url}")
        return "\n".join(lines)

    async def _get_client(self) -> CDPClient:
        """
        Get or create a CDPClient connected to the first page tab.

        Uses flattened CDP sessions (Target.attachToTarget) so that only
        the browser-level WS URL is required. This works with Puppeteer,
        browserless.io, and standard Chrome --remote-debugging-port setups.

        Target discovery uses Target.getTargets (CDP) rather than /json/list
        because some backends (Puppeteer/browserless) return path-style strings
        in /json/list that are not valid targetIds for Target.attachToTarget.
        """
        if self._client is not None:
            return self._client

        # Strip any tab-specific path — always use the browser-level WS endpoint.
        browser_ws = self._ws_url
        if "/devtools/page/" in browser_ws or "/devtools/browser/" in browser_ws:
            from urllib.parse import urlparse
            p = urlparse(browser_ws)
            browser_ws = f"{p.scheme}://{p.netloc}"

        # Browser-level connection (no session yet).
        browser_client = CDPClient(ws_url=browser_ws, timeout=self._timeout)

        # Use Target.getTargets to get proper UUID targetIds.
        result = await browser_client.send("Target.getTargets")
        targets = result.get("targetInfos", [])
        page_targets = [t for t in targets if t.get("type") == "page"]
        if not page_targets:
            raise RuntimeError(
                "No 'page' type target found. "
                "Open a tab in Chrome with remote debugging enabled."
            )
        target_id = page_targets[0]["targetId"]

        attach_result = await browser_client.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = attach_result.get("sessionId")
        if not session_id:
            raise RuntimeError("Target.attachToTarget returned no sessionId")

        # Reuse the same WS connection, routing page commands via sessionId.
        browser_client._session_id = session_id
        self._client = browser_client
        logger.debug("CDPTool: attached to target %s (session=%s)", target_id, session_id)
        return self._client

    # ------------------------------------------------------------------
    # CDP operations
    # ------------------------------------------------------------------

    async def _navigate(self, url: str) -> str:
        """Navigate the active tab to *url* and wait for load."""
        if not url or not url.startswith(("http://", "https://", "file://", "about:")):
            return f"Error: invalid URL '{url}'"
        try:
            client = await self._get_client()
            result = await client.send("Page.navigate", {"url": url})
            frame_id = result.get("frameId", "")
            # Wait a moment for the page to begin loading
            await asyncio.sleep(0.5)
            # Get current URL to confirm navigation
            loc_result = await client.send("Runtime.evaluate",
                                           {"expression": "window.location.href",
                                            "returnByValue": True})
            current_url = loc_result.get("result", {}).get("value", url)
            return f"Navigated to: {current_url} (frameId={frame_id})"
        except TimeoutError:
            self._client = None  # Force reconnect next call
            return f"Error: navigate timed out after {self._timeout}s"
        except RuntimeError as exc:
            self._client = None
            return f"Error: {exc}"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._navigate failed: %s", exc)
            return f"Error: {exc}"

    async def _screenshot(self) -> str:
        """
        Capture a screenshot of the current tab.
        Returns a summary with base64 PNG prefix (first 200 chars shown in context).
        Full base64 is truncated — the operator should treat this as a visual confirmation.
        """
        try:
            client = await self._get_client()
            result = await client.send("Page.captureScreenshot",
                                       {"format": "png", "quality": 80})
            data_b64 = result.get("data", "")
            if not data_b64:
                return "Error: screenshot returned empty data"
            # Estimate image size from base64 length
            approx_bytes = len(data_b64) * 3 // 4
            kb = approx_bytes // 1024
            preview = data_b64[:200]
            return (
                f"Screenshot captured: ~{kb} KB PNG\n"
                f"base64_preview (first 200 chars): {preview}...\n"
                f"[Full base64 stored via capture_tool_io. "
                f"Total length: {len(data_b64)} chars]"
            )
        except TimeoutError:
            self._client = None
            return f"Error: screenshot timed out after {self._timeout}s"
        except RuntimeError as exc:
            self._client = None
            return f"Error: {exc}"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._screenshot failed: %s", exc)
            return f"Error: {exc}"

    async def _dom_snapshot(self) -> str:
        """
        Capture a compact DOM/accessibility snapshot of the current page.
        Uses the Accessibility domain to return a flat, token-efficient tree.
        """
        try:
            client = await self._get_client()
            # Fetch the accessibility tree (compact format)
            result = await client.send("Accessibility.getFullAXTree", {})
            nodes = result.get("nodes", [])
            if not nodes:
                # Fallback to evaluating document.title + body text
                title_r = await client.send("Runtime.evaluate",
                                            {"expression": "document.title", "returnByValue": True})
                body_r = await client.send("Runtime.evaluate",
                                           {"expression": "document.body?.innerText?.slice(0, 2000)",
                                            "returnByValue": True})
                title = title_r.get("result", {}).get("value", "")
                body = body_r.get("result", {}).get("value", "")
                return f"Title: {title}\n\nBody text (first 2000 chars):\n{body}"

            # Build a compact representation
            lines = [f"DOM snapshot — {len(nodes)} accessibility nodes:"]
            _SKIP_ROLES = {"none", "generic", "InlineTextBox", "StaticText"}
            count = 0
            for node in nodes:
                role = node.get("role", {}).get("value", "")
                name = node.get("name", {}).get("value", "")
                node_id = node.get("nodeId", "")
                if role in _SKIP_ROLES or not name:
                    continue
                lines.append(f"  [{node_id}] role={role} name={name!r}")
                count += 1
                if count >= 200:  # cap at 200 nodes in summary
                    lines.append(f"  ... (truncated, total={len(nodes)} nodes)")
                    break

            return "\n".join(lines)
        except TimeoutError:
            self._client = None
            return f"Error: dom_snapshot timed out after {self._timeout}s"
        except RuntimeError as exc:
            self._client = None
            return f"Error: {exc}"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._dom_snapshot failed: %s", exc)
            return f"Error: {exc}"

    async def _click(self, selector: str) -> str:
        """Click the first element matching *selector* (CSS)."""
        if not selector:
            return "Error: selector required for cdp.click"
        try:
            client = await self._get_client()
            # Find element via Runtime.evaluate + querySelector
            find_result = await client.send("Runtime.evaluate", {
                "expression": (
                    f"JSON.stringify((function() {{"
                    f"  var el = document.querySelector({json.dumps(selector)});"
                    f"  if (!el) return null;"
                    f"  var r = el.getBoundingClientRect();"
                    f"  return {{x: r.left + r.width/2, y: r.top + r.height/2}};"
                    f"}}()))"
                ),
                "returnByValue": True,
            })
            val = find_result.get("result", {}).get("value")
            if val is None or val == "null":
                return f"Error: no element found for selector '{selector}'"

            coords = json.loads(val)
            x, y = coords["x"], coords["y"]

            # Dispatch mouse click via Input domain
            for phase, button_type in (("mousePressed", "left"), ("mouseReleased", "left")):
                await client.send("Input.dispatchMouseEvent", {
                    "type": phase,
                    "x": x, "y": y,
                    "button": button_type,
                    "clickCount": 1,
                })
            return f"Clicked '{selector}' at ({x:.0f}, {y:.0f})"
        except TimeoutError:
            self._client = None
            return f"Error: click timed out after {self._timeout}s"
        except RuntimeError as exc:
            self._client = None
            return f"Error: {exc}"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._click failed: %s", exc)
            return f"Error: {exc}"

    async def _type(self, inp: Any) -> str:
        """
        Type *text* into the element matching *selector*.

        Input: {"selector": "#search", "text": "hello world"}
             or a plain string (types into currently focused element).
        """
        if isinstance(inp, dict):
            selector = inp.get("selector", "")
            text = str(inp.get("text", ""))
        elif isinstance(inp, str):
            # Try to parse as JSON first
            try:
                parsed = json.loads(inp)
                if isinstance(parsed, dict):
                    selector = parsed.get("selector", "")
                    text = str(parsed.get("text", ""))
                else:
                    selector = ""
                    text = inp
            except json.JSONDecodeError:
                selector = ""
                text = inp
        else:
            return "Error: cdp.type input must be {selector, text} or a plain string"

        if not text:
            return "Error: no text to type"

        try:
            client = await self._get_client()

            # Focus the selector if provided
            if selector:
                focus_r = await client.send("Runtime.evaluate", {
                    "expression": (
                        f"(function() {{"
                        f"  var el = document.querySelector({json.dumps(selector)});"
                        f"  if (!el) return 'not_found';"
                        f"  el.focus();"
                        f"  return 'ok';"
                        f"}}())"
                    ),
                    "returnByValue": True,
                })
                if focus_r.get("result", {}).get("value") == "not_found":
                    return f"Error: no element found for selector '{selector}'"

            # Dispatch keystrokes via Input.dispatchKeyEvent
            for char in text:
                for key_type in ("keyDown", "keyUp"):
                    await client.send("Input.dispatchKeyEvent", {
                        "type": key_type,
                        "text": char,
                        "key": char,
                        "code": f"Key{char.upper()}" if char.isalpha() else "Unidentified",
                    })

            target_desc = f"'{selector}'" if selector else "focused element"
            return f"Typed {len(text)} chars into {target_desc}"
        except TimeoutError:
            self._client = None
            return f"Error: type timed out after {self._timeout}s"
        except RuntimeError as exc:
            self._client = None
            return f"Error: {exc}"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._type failed: %s", exc)
            return f"Error: {exc}"

    async def _scroll(self, inp: Any) -> str:
        """
        Scroll the page.

        Input: {"x": 0, "y": 500}  or plain int (y offset).
        """
        if isinstance(inp, (int, float)):
            x, y = 0, int(inp)
        elif isinstance(inp, dict):
            x = int(inp.get("x", 0))
            y = int(inp.get("y", 0))
        elif isinstance(inp, str):
            try:
                parsed = json.loads(inp)
                if isinstance(parsed, dict):
                    x = int(parsed.get("x", 0))
                    y = int(parsed.get("y", 0))
                else:
                    x, y = 0, int(parsed)
            except (json.JSONDecodeError, ValueError):
                return "Error: cdp.scroll input must be {x, y} or an integer"
        else:
            return "Error: cdp.scroll input must be {x, y} or an integer"

        try:
            client = await self._get_client()
            await client.send("Runtime.evaluate", {
                "expression": f"window.scrollTo({x}, {y})",
                "returnByValue": True,
            })
            return f"Scrolled to ({x}, {y})"
        except TimeoutError:
            self._client = None
            return f"Error: scroll timed out after {self._timeout}s"
        except RuntimeError as exc:
            self._client = None
            return f"Error: {exc}"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._scroll failed: %s", exc)
            return f"Error: {exc}"

    async def _eval(self, expression: str) -> str:
        """Evaluate a JavaScript expression in the page context."""
        if not expression:
            return "Error: expression required for cdp.eval"
        try:
            client = await self._get_client()
            result = await client.send("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            })
            val = result.get("result", {})
            if val.get("type") == "undefined":
                return "undefined"
            if "value" in val:
                v = val["value"]
                return json.dumps(v) if not isinstance(v, str) else v
            # Exception from JS
            exc_details = result.get("exceptionDetails")
            if exc_details:
                msg = exc_details.get("text", "unknown error")
                return f"Error (JS): {msg}"
            return json.dumps(val)
        except TimeoutError:
            self._client = None
            return f"Error: eval timed out after {self._timeout}s"
        except RuntimeError as exc:
            self._client = None
            return f"Error: {exc}"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._eval failed: %s", exc)
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # PHASE 2: Network, Performance, Storage
    # ------------------------------------------------------------------

    async def _network(self, inp: Any) -> str:
        """Capture recent network requests/responses with optional filtering."""
        try:
            if isinstance(inp, dict):
                action = inp.get("action", "capture")
                limit = inp.get("limit", 50)
            else:
                action = "capture"
                limit = 50

            if action != "capture":
                return f"Error: unknown network action '{action}' (use 'capture')"

            client = await self._get_client()
            # Enable Network domain and request interception
            await client.send("Network.enable", {})

            # Retrieve network requests (returns cached requests)
            # We use Runtime.evaluate to fetch from the Resource Timing API
            expr = """(function(){
  const requests = performance.getEntriesByType('resource').map(r => ({
    name: r.name,
    type: r.initiatorType,
    duration: Math.round(r.duration),
    size: r.transferSize || 0,
    status: 'unknown'
  }));
  const navs = performance.getEntriesByType('navigation');
  return {document: navs[0], requests: requests.slice(0, 50)};
})()"""
            result = await client.send("Runtime.evaluate", {
                "expression": expr,
                "returnByValue": True,
            })
            data = result.get("result", {}).get("value", {})
            lines = ["Network requests (from Resource Timing API):"]
            if data.get("document"):
                doc = data["document"]
                lines.append(f"  Navigation: domInteractive={doc.get('domInteractive', 0)}ms, loadEventEnd={doc.get('loadEventEnd', 0)}ms")
            for req in data.get("requests", [])[:limit]:
                lines.append(f"  {req['name'][:80]} ({req['type']}) {req['duration']:.0f}ms, {req['size']} bytes")
            if not data.get("requests"):
                lines.append("  (no requests captured yet)")
            return "\n".join(lines)
        except TimeoutError:
            self._client = None
            return f"Error: network capture timed out after {self._timeout}s"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._network failed: %s", exc)
            return f"Error: {exc}"

    async def _console(self) -> str:
        """Capture console logs (messages, warnings, errors) from the page."""
        try:
            client = await self._get_client()
            # Enable Runtime domain for console messages
            await client.send("Runtime.enable", {})

            # Evaluate a script that returns console history
            # (Note: actual CDP doesn't persist console — we capture via JS)
            expr = """(function(){
  const logs = window.__cdp_console_logs = window.__cdp_console_logs || [];
  // Hook console methods
  ['log','warn','error','info','debug'].forEach(method => {
    const orig = console[method];
    console[method] = function(...args) {
      const msg = args.map(a => {
        if (typeof a === 'object') return JSON.stringify(a);
        return String(a);
      }).join(' ');
      logs.push({method, msg, ts: new Date().toISOString()});
      if (logs.length > 100) logs.shift();
      return orig.apply(console, args);
    };
  });
  return logs.length;
})()"""
            await client.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})

            # Now read the logs
            expr2 = "window.__cdp_console_logs || []"
            result = await client.send("Runtime.evaluate", {
                "expression": expr2,
                "returnByValue": True,
            })
            logs = result.get("result", {}).get("value", [])

            if not logs:
                return "No console logs captured yet. Navigate a page and interact with it."

            lines = [f"Console logs ({len(logs)} entries):"]
            for log in logs[-20:]:  # Last 20 entries
                method = log.get("method", "log").upper()
                msg = log.get("msg", "")[:100]
                lines.append(f"  [{method}] {msg}")
            return "\n".join(lines)
        except Exception as exc:
            self._client = None
            logger.warning("cdp._console failed: %s", exc)
            return f"Error: {exc}"

    async def _performance(self) -> str:
        """Capture Core Web Vitals and performance metrics."""
        try:
            client = await self._get_client()

            expr = """(function(){
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paint = performance.getEntriesByType('paint');
  const fcpEntry = paint.find(p => p.name === 'first-contentful-paint');
  const lcpCandidates = performance.getEntriesByType('largest-contentful-paint');
  const lcp = lcpCandidates[lcpCandidates.length - 1];
  const clsEntries = performance.getEntriesByType('layout-shift').filter(e => !e.hadRecentInput);
  const cls = clsEntries.reduce((sum, e) => sum + e.value, 0);

  return {
    FCP: fcpEntry ? fcpEntry.startTime.toFixed(1) : 'N/A',
    LCP: lcp ? lcp.renderTime.toFixed(1) : 'N/A',
    CLS: cls.toFixed(3),
    TTFB: (nav.responseStart - nav.requestStart).toFixed(1),
    DOMContentLoaded: nav.domContentLoadedEventEnd ? (nav.domContentLoadedEventEnd - nav.navigationStart).toFixed(1) : 'N/A',
    LoadComplete: nav.loadEventEnd ? (nav.loadEventEnd - nav.navigationStart).toFixed(1) : 'N/A',
  };
})()"""
            result = await client.send("Runtime.evaluate", {
                "expression": expr,
                "returnByValue": True,
            })
            metrics = result.get("result", {}).get("value", {})

            lines = ["Core Web Vitals & Performance:"]
            for key, val in metrics.items():
                unit = "ms" if key != "CLS" else ""
                lines.append(f"  {key}: {val}{unit}")
            return "\n".join(lines)
        except Exception as exc:
            self._client = None
            logger.warning("cdp._performance failed: %s", exc)
            return f"Error: {exc}"

    async def _cookies(self, inp: Any) -> str:
        """List or delete cookies."""
        try:
            if isinstance(inp, dict):
                action = inp.get("action", "list")
                name_filter = inp.get("name")
            else:
                action = "list"
                name_filter = None

            client = await self._get_client()

            if action == "list":
                # Get cookies via HTTP (simpler than CDP Network.getCookies)
                async with httpx.AsyncClient(timeout=self._timeout) as http_client:
                    resp = await http_client.get(f"{self._http_base}/json/list")
                    resp.raise_for_status()
                    tabs = resp.json()
                    tab = next((t for t in tabs if t.get("type") == "page"), None)
                    if not tab:
                        return "Error: no page tab found"

                # Use CDP to get cookies for current URL
                expr = """(function(){
  return document.cookie.split('; ').map(c => {
    const [name, value] = c.split('=');
    return {name, value};
  });
})()"""
                result = await client.send("Runtime.evaluate", {
                    "expression": expr,
                    "returnByValue": True,
                })
                cookies = result.get("result", {}).get("value", [])

                if not cookies:
                    return "No cookies found."

                lines = [f"Cookies ({len(cookies)}):"]
                for cookie in cookies:
                    name = cookie.get("name", "")
                    value = cookie.get("value", "")[:60]
                    lines.append(f"  {name}={value}...")
                return "\n".join(lines)
            elif action == "clear":
                expr = "document.cookie.split('; ').forEach(c => document.cookie = c.split('=')[0] + '=;expires=' + new Date().toUTCString())"
                await client.send("Runtime.evaluate", {"expression": expr})
                return "Cookies cleared."
            else:
                return f"Error: unknown cookie action '{action}' (use 'list' or 'clear')"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._cookies failed: %s", exc)
            return f"Error: {exc}"

    async def _storage(self, inp: Any) -> str:
        """List or clear localStorage, sessionStorage, indexedDB."""
        try:
            if isinstance(inp, dict):
                action = inp.get("action", "list")
                store_type = inp.get("type", "localStorage")
            else:
                action = "list"
                store_type = "localStorage"

            client = await self._get_client()

            if action == "list":
                if store_type == "localStorage":
                    expr = "Object.entries(localStorage).slice(0, 20)"
                elif store_type == "sessionStorage":
                    expr = "Object.entries(sessionStorage).slice(0, 20)"
                else:
                    return f"Error: unknown storage type '{store_type}'"

                result = await client.send("Runtime.evaluate", {
                    "expression": expr,
                    "returnByValue": True,
                })
                items = result.get("result", {}).get("value", [])

                if not items:
                    return f"No items in {store_type}."

                lines = [f"{store_type} ({len(items)} entries):"]
                for key, value in items:
                    val_str = str(value)[:80]
                    lines.append(f"  {key}: {val_str}")
                return "\n".join(lines)
            elif action == "clear":
                expr = f"{store_type}.clear()"
                await client.send("Runtime.evaluate", {"expression": expr})
                return f"{store_type} cleared."
            else:
                return f"Error: unknown action '{action}' (use 'list' or 'clear')"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._storage failed: %s", exc)
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # PHASE 3: IndexedDB, Service Workers, Cache, Throttling
    # ------------------------------------------------------------------

    async def _indexeddb(self, inp: Any) -> str:
        """List IndexedDB databases and object stores."""
        try:
            if isinstance(inp, dict):
                action = inp.get("action", "list")
                db_name = inp.get("db")
            else:
                action = "list"
                db_name = None

            client = await self._get_client()

            if action == "list":
                # Use Storage domain to enumerate IndexedDBs
                result = await client.send("Storage.getIndexedDBDatabaseNames", {})
                db_names = result.get("databaseNames", [])

                if not db_names:
                    return "No IndexedDB databases found."

                lines = [f"IndexedDB databases ({len(db_names)}):"]
                for db in db_names[:20]:  # Limit to first 20
                    lines.append(f"  • {db}")

                # Try to get key counts
                expr = f"""(function(){{
  const dbs = await indexedDB.databases();
  const details = {{}};
  for (const db of dbs) {{
    try {{
      const req = indexedDB.open(db.name);
      req.onsuccess = () => {{
        const conn = req.result;
        const stores = Array.from(conn.objectStoreNames);
        details[db.name] = stores;
        conn.close();
      }};
    }} catch(e) {{}}
  }}
  return details;
}})()"""
                # Note: Promise-based indexedDB is complex; simplified for MVP
                return "\n".join(lines) + "\n(Use Inspector for detailed store inspection)"

            elif action == "clear":
                if not db_name:
                    return "Error: db name required for clear action"
                expr = f"await indexedDB.deleteDatabase('{db_name}')"
                await client.send("Runtime.evaluate", {
                    "expression": expr,
                    "returnByValue": True,
                    "awaitPromise": True,
                })
                return f"IndexedDB '{db_name}' cleared."
            else:
                return f"Error: unknown indexeddb action '{action}'"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._indexeddb failed: %s", exc)
            return f"Error: {exc}"

    async def _service_worker(self, inp: Any) -> str:
        """List or unregister Service Workers."""
        try:
            if isinstance(inp, dict):
                action = inp.get("action", "list")
            else:
                action = "list"

            client = await self._get_client()

            if action == "list":
                # Service Worker API is accessible via Runtime.evaluate
                expr = """(function(){
  if (!navigator.serviceWorker) return {error: 'Service Workers not supported'};
  return navigator.serviceWorker.getRegistrations().then(regs => {
    return regs.map(r => ({
      scope: r.scope,
      updateViaCache: r.updateViaCache,
      active: r.active ? r.active.state : 'none',
      waiting: r.waiting ? r.waiting.state : 'none'
    }));
  });
})()"""
                result = await client.send("Runtime.evaluate", {
                    "expression": expr,
                    "returnByValue": True,
                    "awaitPromise": True,
                })
                data = result.get("result", {}).get("value")

                if isinstance(data, dict) and "error" in data:
                    return data["error"]

                if not data:
                    return "No Service Workers registered."

                lines = [f"Service Workers ({len(data)}):"]
                for sw in data:
                    lines.append(f"  Scope: {sw.get('scope', '?')}")
                    lines.append(f"    Active: {sw.get('active', '?')}, Waiting: {sw.get('waiting', '?')}")
                return "\n".join(lines)

            elif action == "unregister":
                expr = """navigator.serviceWorker.getRegistrations().then(regs =>
                  Promise.all(regs.map(r => r.unregister()))
                ).then(results => results.length)"""
                result = await client.send("Runtime.evaluate", {
                    "expression": expr,
                    "returnByValue": True,
                    "awaitPromise": True,
                })
                count = result.get("result", {}).get("value", 0)
                return f"Unregistered {count} Service Worker(s)."
            else:
                return f"Error: unknown service_worker action '{action}'"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._service_worker failed: %s", exc)
            return f"Error: {exc}"

    async def _cache(self, inp: Any) -> str:
        """List or clear Cache Storage (Service Worker caches)."""
        try:
            if isinstance(inp, dict):
                action = inp.get("action", "list")
                cache_name = inp.get("name")
            else:
                action = "list"
                cache_name = None

            client = await self._get_client()

            if action == "list":
                expr = """caches.keys().then(names =>
                  Promise.all(names.map(name =>
                    caches.open(name).then(cache =>
                      cache.keys().then(reqs => ({name, count: reqs.length}))
                    )
                  ))
                )"""
                result = await client.send("Runtime.evaluate", {
                    "expression": expr,
                    "returnByValue": True,
                    "awaitPromise": True,
                })
                caches = result.get("result", {}).get("value", [])

                if not caches:
                    return "No cache stores found."

                lines = [f"Cache stores ({len(caches)}):"]
                for cache in caches:
                    lines.append(f"  {cache.get('name', '?')} — {cache.get('count', 0)} entries")
                return "\n".join(lines)

            elif action == "clear":
                if not cache_name:
                    expr = "caches.keys().then(names => Promise.all(names.map(n => caches.delete(n)))).then(results => results.length)"
                else:
                    expr = f"caches.delete('{cache_name}').then(ok => ok ? 1 : 0)"
                result = await client.send("Runtime.evaluate", {
                    "expression": expr,
                    "returnByValue": True,
                    "awaitPromise": True,
                })
                count = result.get("result", {}).get("value", 0)
                return f"Cleared {count} cache(s)."
            else:
                return f"Error: unknown cache action '{action}'"
        except Exception as exc:
            self._client = None
            logger.warning("cdp._cache failed: %s", exc)
            return f"Error: {exc}"

    async def _throttle(self, inp: Any) -> str:
        """Set network throttling to simulate slow connections."""
        try:
            if isinstance(inp, dict):
                action = inp.get("action", "set")
                latency = inp.get("latency", 0)  # ms
                download = inp.get("download", -1)  # kbps, -1 = no throttle
                upload = inp.get("upload", -1)     # kbps
            else:
                action = "set"
                latency = 0
                download = -1
                upload = -1

            client = await self._get_client()

            if action == "set":
                # Enable Network domain if not already
                await client.send("Network.enable", {})
                # Set throttling
                await client.send("Network.emulateNetworkConditions", {
                    "offline": False,
                    "downloadThroughput": download,  # -1 to disable
                    "uploadThroughput": upload,      # -1 to disable
                    "latency": latency,              # ms
                })
                return f"Network throttling set: {latency}ms latency, {download} kbps down, {upload} kbps up"

            elif action == "reset":
                # Reset to no throttling
                await client.send("Network.emulateNetworkConditions", {
                    "offline": False,
                    "downloadThroughput": -1,
                    "uploadThroughput": -1,
                    "latency": 0,
                })
                return "Network throttling disabled."

            elif action == "offline":
                # Simulate offline mode
                await client.send("Network.emulateNetworkConditions", {
                    "offline": True,
                    "downloadThroughput": -1,
                    "uploadThroughput": -1,
                    "latency": 0,
                })
                return "Network set to offline mode."

            else:
                return f"Error: unknown throttle action '{action}' (use 'set', 'reset', or 'offline')"

        except Exception as exc:
            self._client = None
            logger.warning("cdp._throttle failed: %s", exc)
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # MIMIC MODE: Link host cookies & inject browser fingerprint headers
    # ------------------------------------------------------------------

    def _get_chrome_cookies_path(self) -> str | None:
        """Find the user's Chrome cookies database (platform-aware)."""
        import os
        import platform
        from pathlib import Path

        system = platform.system()
        home = Path.home()

        # Try standard Chrome paths
        paths = []
        if system == "Linux":
            paths = [
                home / ".config/google-chrome/Default/Cookies",
                home / ".config/chromium/Default/Cookies",
                home / ".config/google-chrome-stable/Default/Cookies",
            ]
        elif system == "Darwin":  # macOS
            paths = [
                home / "Library/Application Support/Google Chrome/Default/Cookies",
                home / "Library/Application Support/Chromium/Default/Cookies",
            ]
        elif system == "Windows":
            paths = [
                Path(os.environ.get("APPDATA", "")) / "Google/Chrome/User Data/Default/Cookies",
                Path(os.environ.get("APPDATA", "")) / "Chromium/User Data/Default/Cookies",
            ]

        for path in paths:
            if path.exists():
                logger.debug("Found Chrome cookies at %s", path)
                return str(path)

        return None

    def _get_cdp_user_data_dir(self) -> str | None:
        """Find the CDP Chrome's user-data-dir. Try common paths."""
        import os
        from pathlib import Path

        paths = [
            "/tmp/beigebox-cdp",  # Default from docstring
            Path.home() / ".beigebox-cdp",
            "/tmp/beigebox_cdp_profile",
            os.environ.get("CDP_USER_DATA_DIR", ""),
        ]

        for path_str in paths:
            if not path_str:
                continue
            path = Path(path_str)
            # Check if Default/Cookies exists or if Default dir exists
            if (path / "Default").exists():
                logger.debug("Found CDP user-data-dir at %s", path)
                return str(path)

        # Return best guess if nothing found
        return "/tmp/beigebox-cdp"

    def _get_browser_fingerprint(self) -> dict:
        """Extract browser fingerprinting params: UA, headers, viewport, timezone, locale."""
        import platform
        system = platform.system()

        # Standard Chrome User-Agent variants
        ua_map = {
            "Linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Darwin": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        return {
            "user_agent": ua_map.get(system, ua_map["Linux"]),
            "headers": {
                "User-Agent": ua_map.get(system, ua_map["Linux"]),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
            # Viewport: 1920x1080 (common desktop)
            "viewport": {
                "width": 1920,
                "height": 1080,
                "deviceScaleFactor": 1,
                "mobile": False,
                "hasTouch": False,
            },
            # Timezone: UTC (neutral default)
            "timezone": "UTC",
            # Locale: en-US
            "locale": "en-US",
        }

    async def _sync_host_storage(self, client: CDPClient) -> dict:
        """Read localStorage and sessionStorage from host browser via direct JS access."""
        result = {"localStorage": {}, "sessionStorage": {}}

        try:
            # Read localStorage
            expr_local = """
            Object.fromEntries(
              Object.entries(localStorage).map(([k, v]) => [k, v]).slice(0, 100)
            )
            """
            res_local = await client.send("Runtime.evaluate", {
                "expression": expr_local,
                "returnByValue": True,
            })
            if res_local.get("result", {}).get("value"):
                result["localStorage"] = res_local["result"]["value"]
                logger.debug("Read %d localStorage items", len(result["localStorage"]))
        except Exception as e:
            logger.warning("Failed to read localStorage: %s", e)

        try:
            # Read sessionStorage
            expr_session = """
            Object.fromEntries(
              Object.entries(sessionStorage).map(([k, v]) => [k, v]).slice(0, 100)
            )
            """
            res_session = await client.send("Runtime.evaluate", {
                "expression": expr_session,
                "returnByValue": True,
            })
            if res_session.get("result", {}).get("value"):
                result["sessionStorage"] = res_session["result"]["value"]
                logger.debug("Read %d sessionStorage items", len(result["sessionStorage"]))
        except Exception as e:
            logger.warning("Failed to read sessionStorage: %s", e)

        return result

    async def _inject_host_storage(self, client: CDPClient, storage: dict) -> None:
        """Inject localStorage and sessionStorage into CDP Chrome."""
        # Inject localStorage
        if storage.get("localStorage"):
            for key, value in storage["localStorage"].items():
                try:
                    expr = f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)})"
                    await client.send("Runtime.evaluate", {"expression": expr})
                except Exception as e:
                    logger.warning("Failed to set localStorage[%s]: %s", key, e)

        # Inject sessionStorage
        if storage.get("sessionStorage"):
            for key, value in storage["sessionStorage"].items():
                try:
                    expr = f"sessionStorage.setItem({json.dumps(key)}, {json.dumps(value)})"
                    await client.send("Runtime.evaluate", {"expression": expr})
                except Exception as e:
                    logger.warning("Failed to set sessionStorage[%s]: %s", key, e)

        logger.info("Injected %d localStorage + %d sessionStorage items",
                   len(storage.get("localStorage", {})),
                   len(storage.get("sessionStorage", {})))

    async def _mimic_activate(self) -> str:
        """Link user's Chrome cookies into CDP Chrome and inject headers."""
        import os
        from pathlib import Path

        if self._mimic_active:
            return "Mimic mode already active."

        try:
            # Find source (user's Chrome cookies)
            user_cookies = self._get_chrome_cookies_path()
            if not user_cookies or not Path(user_cookies).exists():
                return (
                    "Error: could not find Chrome cookies. "
                    "Ensure Chrome/Chromium is installed with a profile. "
                    "Checked: ~/.config/google-chrome/Default/Cookies (Linux), "
                    "~/Library/Application Support/Google Chrome/Default/Cookies (macOS)"
                )

            # Find destination (CDP Chrome's user-data-dir)
            cdp_user_data = self._get_cdp_user_data_dir()
            if not cdp_user_data:
                return "Error: could not determine CDP Chrome's user-data-dir. Ensure Chrome is running with --user-data-dir=/tmp/beigebox-cdp"

            cdp_cookies_dir = Path(cdp_user_data) / "Default"
            cdp_cookies_dir.mkdir(parents=True, exist_ok=True)
            cdp_cookies_path = cdp_cookies_dir / "Cookies"

            # Back up existing cookies if any
            if cdp_cookies_path.exists():
                backup_path = cdp_cookies_path.with_suffix(".bak")
                if not backup_path.exists():
                    os.rename(str(cdp_cookies_path), str(backup_path))
                    logger.info("Backed up existing CDP cookies to %s", backup_path)
                    self._mimic_symlinks.append(str(backup_path))

            # Create symlink: CDP cookies → user's cookies
            os.symlink(user_cookies, str(cdp_cookies_path))
            self._mimic_symlinks.append(str(cdp_cookies_path))
            logger.info("Linked CDP cookies: %s → %s", cdp_cookies_path, user_cookies)

            # Inject browser fingerprinting via CDP
            import platform
            client = await self._get_client()
            fp = self._get_browser_fingerprint()

            # Enable required domains
            await client.send("Network.enable", {})
            await client.send("Emulation.enable", {})

            # Determine platform string
            system = platform.system()
            platform_str = "Linux" if system == "Linux" else ("macOS" if system == "Darwin" else "Windows")

            # Set User-Agent override
            await client.send("Network.setUserAgentOverride", {
                "userAgent": fp["user_agent"],
                "platform": "",
                "userAgentMetadata": {
                    "platform": platform_str,
                    "platformVersion": "",
                    "architecture": "x86",
                    "model": "",
                    "mobile": False,
                }
            })
            logger.info("Set User-Agent via CDP")

            # Set extra HTTP headers
            await client.send("Network.setExtraHTTPHeaders", {
                "headers": fp["headers"]
            })
            logger.info("Set HTTP headers via CDP")

            # Set device metrics (viewport, DPR)
            await client.send("Emulation.setDeviceMetricsOverride", {
                "width": fp["viewport"]["width"],
                "height": fp["viewport"]["height"],
                "deviceScaleFactor": fp["viewport"]["deviceScaleFactor"],
                "mobile": fp["viewport"]["mobile"],
                "hasTouch": fp["viewport"]["hasTouch"],
            })
            logger.info("Set device metrics (viewport=%dx%d) via CDP",
                       fp["viewport"]["width"], fp["viewport"]["height"])

            # Set timezone override
            await client.send("Emulation.setTimezoneOverride", {
                "timezoneId": fp["timezone"]
            })
            logger.info("Set timezone to %s via CDP", fp["timezone"])

            # Set locale override
            await client.send("Emulation.setLocaleOverride", {
                "locale": fp["locale"]
            })
            logger.info("Set locale to %s via CDP", fp["locale"])

            # Sync host storage (localStorage, sessionStorage)
            storage = await self._sync_host_storage(client)
            await self._inject_host_storage(client, storage)

            self._mimic_active = True
            summary = (
                f"✓ Mimic mode activated:\n"
                f"  Cookies linked: {user_cookies}\n"
                f"  User-Agent: {fp['user_agent'][:50]}...\n"
                f"  Viewport: {fp['viewport']['width']}x{fp['viewport']['height']}\n"
                f"  Timezone: {fp['timezone']}\n"
                f"  Locale: {fp['locale']}\n"
                f"  Headers: {', '.join(fp['headers'].keys())}\n"
                f"  Storage: {len(storage.get('localStorage', {}))} localStorage + {len(storage.get('sessionStorage', {}))} sessionStorage items"
            )
            return summary

        except Exception as exc:
            logger.error("mimic_activate failed: %s", exc)
            # Attempt cleanup on error
            for path in self._mimic_symlinks:
                try:
                    p = Path(path)
                    if p.is_symlink():
                        p.unlink()
                except Exception as e:
                    logger.warning("Failed to clean up %s: %s", path, e)
            self._mimic_symlinks = []
            self._mimic_active = False
            return f"Error: {exc}"

    async def _mimic_deactivate(self) -> str:
        """Tear down mimic mode: remove symlinks and reset headers."""
        import os
        from pathlib import Path

        if not self._mimic_active:
            return "Mimic mode not active."

        try:
            results = []

            # Remove symlinks
            for path_str in self._mimic_symlinks:
                try:
                    path = Path(path_str)
                    if path.is_symlink():
                        path.unlink()
                        logger.info("Removed symlink: %s", path)
                        results.append(f"Removed: {path}")
                    elif path.suffix == ".bak":
                        # Restore backed-up cookies
                        original = path.with_suffix("")
                        if original.exists():
                            os.remove(str(original))
                        os.rename(str(path), str(original))
                        logger.info("Restored backed-up cookies: %s", original)
                        results.append(f"Restored: {original}")
                except Exception as e:
                    logger.warning("Failed to clean up %s: %s", path_str, e)
                    results.append(f"Failed to remove {path_str}: {e}")

            # Reset fingerprint via CDP
            try:
                client = await self._get_client()
                # Clear User-Agent override
                await client.send("Network.setUserAgentOverride", {"userAgent": ""})
                # Clear extra headers
                await client.send("Network.setExtraHTTPHeaders", {"headers": {}})
                # Reset device metrics
                await client.send("Emulation.clearDeviceMetricsOverride", {})
                # Reset timezone
                await client.send("Emulation.clearTimezoneOverride", {})
                # Reset locale
                await client.send("Emulation.clearLocaleOverride", {})
                logger.info("Reset CDP fingerprinting to defaults")
                results.append("Reset fingerprinting to defaults")

                # Clear storage
                try:
                    await client.send("Runtime.evaluate", {"expression": "localStorage.clear()"})
                    await client.send("Runtime.evaluate", {"expression": "sessionStorage.clear()"})
                    logger.info("Cleared CDP storage")
                    results.append("Cleared storage")
                except Exception as se:
                    logger.warning("Failed to clear storage: %s", se)
                    results.append(f"Warning: could not clear storage: {se}")

            except Exception as e:
                logger.warning("Failed to reset fingerprinting: %s", e)
                results.append(f"Warning: could not reset fingerprinting: {e}")

            self._mimic_active = False
            self._mimic_symlinks = []

            summary = "✓ Mimic mode deactivated:\n" + "\n".join(f"  {r}" for r in results)
            return summary

        except Exception as exc:
            logger.error("mimic_deactivate failed: %s", exc)
            return f"Error: {exc}"
