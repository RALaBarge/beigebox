"""
Tool registry: central dispatch for all tools.
Reads config.yaml to determine which tools are enabled.
New tools are added here + in config.yaml. Nothing else changes.

Now with:
  - Calculator, DateTime, SystemInfo, Memory tools
  - Webhook notifier for monitoring tool invocations
"""

import logging
import time
from beigebox.config import get_config
from beigebox.tools.web_search import WebSearchTool
from beigebox.tools.web_scraper import WebScraperTool
from beigebox.tools.google_search import GoogleSearchTool
from beigebox.tools.calculator import CalculatorTool
from beigebox.tools.datetime_tool import DateTimeTool
from beigebox.tools.system_info import SystemInfoTool
from beigebox.tools.memory import MemoryTool
from beigebox.tools.notifier import ToolNotifier

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Manages available tools based on configuration."""

    def __init__(self, vector_store=None):
        self.tools: dict[str, object] = {}
        cfg = get_config()
        tools_cfg = cfg.get("tools", {})

        # Webhook notifier (optional)
        webhook_url = tools_cfg.get("webhook_url", "")
        self.notifier = ToolNotifier(webhook_url)

        if not tools_cfg.get("enabled", False):
            logger.info("Tools disabled globally")
            return

        # --- Web Search ---
        ws_cfg = tools_cfg.get("web_search", {})
        if ws_cfg.get("enabled", False):
            provider = ws_cfg.get("provider", "duckduckgo")
            if provider == "duckduckgo":
                self.tools["web_search"] = WebSearchTool(
                    max_results=ws_cfg.get("max_results", 5)
                )
            elif provider == "google":
                gs_cfg = tools_cfg.get("google_search", {})
                self.tools["web_search"] = GoogleSearchTool(
                    api_key=gs_cfg.get("api_key", ""),
                    cse_id=gs_cfg.get("cse_id", ""),
                    max_results=ws_cfg.get("max_results", 5),
                )

        # --- Web Scraper ---
        sc_cfg = tools_cfg.get("web_scraper", {})
        if sc_cfg.get("enabled", False):
            self.tools["web_scraper"] = WebScraperTool(
                max_content_length=sc_cfg.get("max_content_length", 10000)
            )

        # --- Google Search (as separate tool even when DDG is primary) ---
        gs_cfg = tools_cfg.get("google_search", {})
        if gs_cfg.get("enabled", False):
            self.tools["google_search"] = GoogleSearchTool(
                api_key=gs_cfg.get("api_key", ""),
                cse_id=gs_cfg.get("cse_id", ""),
                max_results=gs_cfg.get("max_results", 5),
            )

        # --- Calculator ---
        calc_cfg = tools_cfg.get("calculator", {})
        if calc_cfg.get("enabled", True):  # Enabled by default — no deps
            self.tools["calculator"] = CalculatorTool()

        # --- DateTime ---
        dt_cfg = tools_cfg.get("datetime", {})
        if dt_cfg.get("enabled", True):  # Enabled by default — no deps
            self.tools["datetime"] = DateTimeTool(
                local_tz_offset=dt_cfg.get("local_tz_offset", -5.0)
            )

        # --- System Info ---
        si_cfg = tools_cfg.get("system_info", {})
        if si_cfg.get("enabled", True):  # Enabled by default — no deps
            self.tools["system_info"] = SystemInfoTool()

        # --- Memory (conversation recall) ---
        mem_cfg = tools_cfg.get("memory", {})
        if mem_cfg.get("enabled", True) and vector_store is not None:
            self.tools["memory"] = MemoryTool(
                vector_store=vector_store,
                max_results=mem_cfg.get("max_results", 3),
                min_score=mem_cfg.get("min_score", 0.3),
            )

        logger.info("Tool registry loaded: %s", list(self.tools.keys()))

    def get(self, name: str):
        """Get a tool by name, or None if not registered."""
        return self.tools.get(name)

    def list_tools(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self.tools.keys())

    def run_tool(self, name: str, input_text: str) -> str | None:
        """
        Run a tool by name. Returns result string or None.
        Sends notification to webhook if configured.
        """
        tool = self.tools.get(name)
        if tool is None:
            logger.warning("Tool '%s' not found in registry", name)
            return None

        start = time.monotonic()
        result = tool.run(input_text)
        elapsed_ms = (time.monotonic() - start) * 1000

        # Notify webhook
        if result is not None:
            self.notifier.notify(name, input_text, result, elapsed_ms)

        return result
