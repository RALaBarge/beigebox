"""
Lightweight registry for security tools — same shape as
beigebox.tools.registry.ToolRegistry (so McpServer can consume it) but
without the broader BeigeBox config/validation/notifier coupling. Pen/sec
tools own their own input parsing + safety in SecurityTool.run.
"""
from __future__ import annotations

import logging
import time

from beigebox.logging import log_tool_call

logger = logging.getLogger(__name__)


class SecurityToolRegistry:
    """Minimal registry: dict of name → tool, with a run_tool method."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def register(self, tool) -> None:
        if not getattr(tool, "name", ""):
            raise ValueError(f"tool {tool!r} has no name")
        if tool.name in self.tools:
            raise ValueError(f"tool '{tool.name}' already registered")
        self.tools[tool.name] = tool

    def get(self, name: str):
        return self.tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self.tools.keys())

    def run_tool(self, name: str, input_text: str) -> str | dict | None:
        tool = self.tools.get(name)
        if tool is None:
            logger.warning("security tool '%s' not found", name)
            return None
        start = time.monotonic()
        try:
            result = tool.run(input_text)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.warning("security tool '%s' raised: %s", name, exc)
            try:
                log_tool_call(name, "error", latency_ms=elapsed_ms)
            except Exception:
                pass
            return f'{{"ok": false, "error": "tool {name} failed: {exc}"}}'
        elapsed_ms = (time.monotonic() - start) * 1000
        try:
            log_tool_call(name, "success", latency_ms=elapsed_ms)
        except Exception:
            pass
        return result


def build_default_registry() -> SecurityToolRegistry:
    """Construct a registry containing every wrapper we ship."""
    from beigebox.security_mcp.tools import ALL_TOOL_FACTORIES

    reg = SecurityToolRegistry()
    for factory in ALL_TOOL_FACTORIES:
        try:
            reg.register(factory())
        except Exception as exc:
            logger.warning("failed to register %s: %s", factory, exc)
    return reg
