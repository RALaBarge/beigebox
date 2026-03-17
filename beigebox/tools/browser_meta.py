"""
BrowserMetaTool — Level 2 lazy-loading wrapper around BrowserboxTool.

"Level 2" means the system prompt only shows a stub description (saves tokens).
The model calls {"action": "discover"} to get the full capability list on demand,
then calls namespaced actions directly once it knows what's available.

Compare with Level 1 (current BrowserboxTool): the full description including all
namespace examples is inlined into the system prompt unconditionally.

This tool also wraps results in ToolResult so the operator gets structured status,
next-step hints, and recovery suggestions on errors.
"""
from __future__ import annotations

import json
import logging

from beigebox.tools.browserbox import BrowserboxTool
from beigebox.tools.result import ToolResult

logger = logging.getLogger(__name__)

# Canonical capability map — returned by discover action.
_NAMESPACES: dict[str, list[str]] = {
    "tabs":    ["open", "screenshot", "close", "list"],
    "dom":     ["snapshot", "get_text", "click", "fill", "submit", "select", "hover"],
    "nav":     ["back", "forward", "reload", "url"],
    "clip":    ["copy", "paste"],
    "storage": ["get", "set", "remove"],
    "fetch":   ["get", "post"],
    "network": ["intercept", "requests"],
    "inject":  ["script", "style"],
    "pdf":     ["extract"],
}

_DISCOVER_HINT = (
    'Call any action: {"action": "dom.snapshot", "input": ""} to see the current page, '
    '{"action": "tabs.open", "input": "https://example.com"} to navigate, '
    '{"action": "dom.get_text", "input": ""} to extract page text.'
)

_ACTION_HINTS: dict[str, str] = {
    "tabs.open":      "Page is loading — call dom.snapshot or dom.get_text to read it",
    "dom.snapshot":   "Use specific CSS selectors from this snapshot in dom.click or dom.fill",
    "nav.back":       "Navigated back — call dom.snapshot to see the current state",
    "nav.forward":    "Navigated forward — call dom.snapshot to see the current state",
    "nav.reload":     "Page reloaded — call dom.snapshot to see the current state",
    "tabs.screenshot": "Screenshot captured — use dom.get_text for extractable text content",
    "dom.click":      "Clicked — call dom.snapshot to see if the page changed",
    "dom.fill":       "Field filled — call dom.submit or dom.click to proceed",
}


class BrowserMetaTool:
    """
    Lazy-loading browser control. Wraps BrowserboxTool.

    Input must be a JSON object with an "action" key and optional "input" key.

    Call {"action": "discover"} to see all available browser actions,
    then call any action directly:
      {"action": "dom.get_text", "input": ""}
      {"action": "tabs.open", "input": "https://example.com"}
      {"action": "dom.click", "input": "#submit"}
    """

    capture_tool_io: bool = True
    max_context_chars: int = 4000

    description = (
        "Control the active browser tab.\n"
        "Input: {\"action\": \"<ns.method>\", \"input\": <value>}\n"
        "First call: {\"action\": \"discover\"} to see all available actions.\n"
        "Quick start: dom.snapshot (page overview), tabs.open (navigate), dom.get_text (text)"
    )

    def __init__(self, browserbox: BrowserboxTool):
        self._bb = browserbox

    def run(self, input_str: str) -> str:
        try:
            params = json.loads(input_str.strip() or "{}")
        except json.JSONDecodeError:
            return str(ToolResult(
                status="error",
                data='Input must be a JSON object: {"action": "ns.method", "input": "..."}',
                recovery_hint='Try: {"action": "discover"} to see all available actions',
            ))

        action = params.get("action", "").strip()

        # discover — return capability map
        if not action or action == "discover":
            return str(ToolResult(
                status="ok",
                data=json.dumps({"namespaces": _NAMESPACES}, indent=2),
                hint=_DISCOVER_HINT,
            ))

        # validate namespace
        ns = action.split(".")[0] if "." in action else action
        if ns not in _NAMESPACES:
            known = ", ".join(_NAMESPACES.keys())
            return str(ToolResult(
                status="error",
                data=f"Unknown namespace '{ns}'",
                recovery_hint=f'Call {{"action": "discover"}} to see valid namespaces. Known: {known}',
            ))

        # proxy to BrowserboxTool
        inp = params.get("input", "")
        bb_call = json.dumps({"tool": action, "input": inp})
        result_str = self._bb.run(bb_call)

        if result_str.startswith("Error:"):
            return str(ToolResult(
                status="error",
                data=result_str,
                recovery_hint=_recovery_hint(action, result_str),
            ))

        return str(ToolResult(
            status="ok",
            data=result_str,
            hint=_ACTION_HINTS.get(action),
        ))


def _recovery_hint(action: str, error: str) -> str:
    lower = error.lower()
    # Check "browser not connected" before generic "relay" — more specific
    if "browser not connected" in lower:
        return "Chrome extension is not connected — open Chrome and check the BrowserBox extension popup"
    if "could not connect" in lower or "relay" in lower:
        return "BrowserBox relay is not running — start ws_relay.py on port 9009 first"
    if "timed out" in lower:
        return "Action timed out — try dom.get_text instead of snapshot, or reduce page complexity"
    return f'Try a different action or call {{"action": "discover"}} to list all available actions'
