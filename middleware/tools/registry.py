"""
Tool registry: central dispatch for all LangChain / custom tools.
Reads config.yaml to determine which tools are enabled.
New tools are added here + in config.yaml. Nothing else changes.
"""

import logging
from middleware.config import get_config
from middleware.tools.web_search import WebSearchTool
from middleware.tools.web_scraper import WebScraperTool
from middleware.tools.google_search import GoogleSearchTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Manages available tools based on configuration."""

    def __init__(self):
        self.tools: dict[str, object] = {}
        cfg = get_config()
        tools_cfg = cfg.get("tools", {})

        if not tools_cfg.get("enabled", False):
            logger.info("Tools disabled globally")
            return

        # Web Search
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

        # Web Scraper
        sc_cfg = tools_cfg.get("web_scraper", {})
        if sc_cfg.get("enabled", False):
            self.tools["web_scraper"] = WebScraperTool(
                max_content_length=sc_cfg.get("max_content_length", 10000)
            )

        # Google Search (as separate tool even when DDG is primary)
        gs_cfg = tools_cfg.get("google_search", {})
        if gs_cfg.get("enabled", False):
            self.tools["google_search"] = GoogleSearchTool(
                api_key=gs_cfg.get("api_key", ""),
                cse_id=gs_cfg.get("cse_id", ""),
                max_results=ws_cfg.get("max_results", 5),
            )

        logger.info("Tool registry loaded: %s", list(self.tools.keys()))

    def get(self, name: str):
        """Get a tool by name, or None if not registered."""
        return self.tools.get(name)

    def list_tools(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self.tools.keys())

    def run_tool(self, name: str, input_text: str) -> str | None:
        """Run a tool by name. Returns result string or None."""
        tool = self.tools.get(name)
        if tool is None:
            logger.warning("Tool '%s' not found in registry", name)
            return None
        return tool.run(input_text)
