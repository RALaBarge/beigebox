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
from beigebox.logging import log_tool_call
from beigebox.tools.web_search import WebSearchTool
from beigebox.tools.web_scraper import WebScraperTool
from beigebox.tools.google_search import GoogleSearchTool
from beigebox.tools.calculator import CalculatorTool
from beigebox.tools.datetime_tool import DateTimeTool
from beigebox.tools.system_info import SystemInfoTool
from beigebox.tools.memory import MemoryTool
from beigebox.tools.document_search import DocumentSearchTool
from beigebox.tools.ensemble import EnsembleTool
from beigebox.tools.notifier import ToolNotifier
from beigebox.tools.pdf_reader import PdfReaderTool
from beigebox.tools.browserbox import BrowserboxTool
from beigebox.tools.cdp import CDPTool
from beigebox.tools.connection_tool import ConnectionTool
from beigebox.tools.python_interpreter import PythonInterpreterTool
from beigebox.tools.workspace_file import WorkspaceFileTool

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
            # Derive the data directory from the SQLite path so scraped HTML
            # lands in the same volume as the rest of persistent storage.
            from beigebox.config import get_storage_paths
            sqlite_path, _ = get_storage_paths()
            import os
            save_dir = os.path.dirname(os.path.abspath(sqlite_path))
            self.tools["web_scraper"] = WebScraperTool(
                max_content_length=sc_cfg.get("max_content_length", 10000),
                save_dir=save_dir,
                vector_store=vector_store,
            )

        # --- Google Search (as separate tool even when DDG is primary) ---
        gs_cfg = tools_cfg.get("google_search", {})
        if gs_cfg.get("enabled", False):
            self.tools["google_search"] = GoogleSearchTool(
                api_key=gs_cfg.get("api_key", ""),
                cse_id=gs_cfg.get("cse_id", ""),
                max_results=gs_cfg.get("max_results", 5),
            )

        # Calculator, DateTime, SystemInfo default to enabled because they have
        # no external dependencies (pure Python stdlib / psutil). They can be
        # individually disabled in config under tools.calculator.enabled: false.
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

        # --- Workspace File (read/write /workspace/out/ — always enabled) ---
        from pathlib import Path as _P
        _ws_cfg = cfg.get("workspace", {})
        _ws_out = _P(__file__).parent.parent.parent / _ws_cfg.get("path", "./workspace") / "out"
        self.tools["workspace_file"] = WorkspaceFileTool(workspace_out=_ws_out)

        # --- Document Search (workspace document RAG) ---
        ds_cfg = tools_cfg.get("document_search", {})
        if ds_cfg.get("enabled", True) and vector_store is not None:
            self.tools["document_search"] = DocumentSearchTool(
                vector_store=vector_store,
                max_results=ds_cfg.get("max_results", 5),
                min_score=ds_cfg.get("min_score", 0.3),
            )

        # --- Memory (conversation recall) ---
        mem_cfg = tools_cfg.get("memory", {})
        if mem_cfg.get("enabled", True) and vector_store is not None:
            self.tools["memory"] = MemoryTool(
                vector_store=vector_store,
                max_results=mem_cfg.get("max_results", 3),
                min_score=mem_cfg.get("min_score", 0.3),
                query_preprocess=mem_cfg.get("query_preprocess", False),
                query_preprocess_model=mem_cfg.get("query_preprocess_model", ""),
                backend_url=cfg.get("backend", {}).get("url", "http://localhost:11434"),
            )

        # --- PDF Reader (pdf_oxide — disabled by default, requires pip install pdf_oxide) ---
        pdf_cfg = tools_cfg.get("pdf_reader", {})
        if pdf_cfg.get("enabled", False):
            from pathlib import Path as _pdf_path
            app_root = _pdf_path(__file__).parent.parent.parent
            ws_in = app_root / cfg.get("workspace", {}).get("path", "./workspace") / "in"
            self.tools["pdf_reader"] = PdfReaderTool(workspace_in=ws_in)

        # --- Ensemble (multi-model voting — disabled by default) ---
        ens_cfg = tools_cfg.get("ensemble", {})
        if ens_cfg.get("enabled", False):
            from beigebox.config import get_config as _gc
            op_cfg = _gc().get("operator", {})
            default_judge = op_cfg.get("model") or _gc().get("backend", {}).get("default_model")
            self.tools["ensemble"] = EnsembleTool(
                judge_model=ens_cfg.get("judge_model") or default_judge,
                max_models=ens_cfg.get("max_models", 6),
            )

        # --- BrowserBox (browser API relay — disabled by default) ---
        bb_cfg = tools_cfg.get("browserbox", {})
        if bb_cfg.get("enabled", False):
            from pathlib import Path as _P
            _app_root = _P(__file__).parent.parent.parent
            _ws_in = _app_root / cfg.get("workspace", {}).get("path", "./workspace") / "in"
            self.tools["browserbox"] = BrowserboxTool(
                ws_url=bb_cfg.get("ws_url", "ws://localhost:9009"),
                timeout=bb_cfg.get("timeout", 10.0),
                workspace_in=_ws_in,
            )

        # --- CDP (Chrome DevTools Protocol — disabled by default) ---
        # Operator can call: {"tool": "cdp.navigate", "input": "https://example.com"}
        cdp_cfg = tools_cfg.get("cdp", {})
        if cdp_cfg.get("enabled", False):
            self.tools["cdp"] = CDPTool(
                ws_url=cdp_cfg.get("ws_url", "ws://localhost:9222"),
                timeout=float(cdp_cfg.get("timeout", 10)),
            )
            logger.info("CDP tool registered (ws_url=%s)", cdp_cfg.get("ws_url"))

        # --- Python Interpreter (TIR — disabled by default, requires bwrap) ---
        # Registered under the key "python" (not "python_interpreter") so the
        # operator can call it as {"tool": "python", "input": "..."}.
        pi_cfg = tools_cfg.get("python_interpreter", {})
        if pi_cfg.get("enabled", False):
            self.tools["python"] = PythonInterpreterTool()

        # Connection tool auto-enables whenever the top-level connections: section
        # is present in config.yaml — no separate enabled flag needed.
        # --- Connections (agentauth — auto-enabled if connections: configured) ---
        conn_cfg = cfg.get("connections", {})
        if conn_cfg:
            try:
                from agentauth import ConnectionRegistry
                conn_registry = ConnectionRegistry(conn_cfg)
                self.tools["connection"] = ConnectionTool(conn_registry)
                logger.info("Connection registry loaded: %s", list(conn_cfg.keys()))
            except Exception as e:
                logger.warning("Connection registry failed to load: %s", e)

        logger.info("Tool registry loaded: %s", list(self.tools.keys()))

        # --- Plugins (auto-discovered from plugins/ directory) ---
        from beigebox.tools.plugin_loader import load_plugins
        from pathlib import Path as _Path
        plugins_dir = _Path(__file__).parent.parent.parent / "plugins"
        plugin_tools = load_plugins(plugins_dir, tools_cfg)
        for name, tool in plugin_tools.items():
            if name in self.tools:
                logger.warning("Plugin '%s' conflicts with built-in tool — skipped", name)
            else:
                self.tools[name] = tool
        if plugin_tools:
            logger.info("Tool registry after plugins: %s", list(self.tools.keys()))

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
        try:
            result = tool.run(input_text)
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.warning("Tool '%s' raised during run: %s", name, e)
            # Log tool failure
            try:
                log_tool_call(name, "error", latency_ms=elapsed_ms)
            except Exception:
                pass
            return f"Error: tool '{name}' failed: {e}"
        elapsed_ms = (time.monotonic() - start) * 1000

        # Notify webhook
        if result is not None:
            self.notifier.notify(name, input_text, result, elapsed_ms)

        # Log tool success
        try:
            log_tool_call(name, "success", latency_ms=elapsed_ms)
        except Exception:
            pass

        return result
