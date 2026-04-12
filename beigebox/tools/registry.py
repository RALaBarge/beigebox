"""
Tool registry: central dispatch for all tools.
Reads config.yaml to determine which tools are enabled.
New tools are added here + in config.yaml. Nothing else changes.

Now with:
  - Calculator, DateTime, SystemInfo, Memory tools
  - Webhook notifier for monitoring tool invocations
  - Parameter validation (Phase 1) to prevent injection attacks
"""

import logging
import time
from beigebox.config import get_config
from beigebox.logging import log_tool_call
from beigebox.tools.validation import ParameterValidator
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
from beigebox.tools.apex_analyzer import ApexAnalyzerTool
from beigebox.tools.confluence_crawler import ConfluenceCrawler
from beigebox.tools.aura_recon import AuraReconTool
from beigebox.tools.sf_ingest import SfIngestTool
from beigebox.tools.atlassian import AtlassianTool
from beigebox.tools.bluetruth import BlueTruthTool
from beigebox.tools.network_audit import NetworkAuditTool
from beigebox.tools.mcp_validator_tool import MCPValidatorTool
from beigebox.tools.api_anomaly_detector_tool import APIAnomalyDetectorTool
from beigebox.tools.memory_validator_tool import MemoryValidatorTool
from beigebox.tools.plan_manager import PlanManagerTool
from beigebox.tools.research_agent import ResearchAgentTool
from beigebox.tools.parallel_research import ParallelResearchTool
from beigebox.tools.evidence_synthesis import EvidenceSynthesisTool
from beigebox.tools.research_agent_flexible import ResearchAgentFlexibleTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Manages available tools based on configuration."""

    def __init__(self, vector_store=None):
        self.tools: dict[str, object] = {}
        cfg = get_config()
        tools_cfg = cfg.get("tools", {})

        # Parameter validation (Phase 1) — prevents injection attacks
        self.validator = ParameterValidator()

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
            default_judge = op_cfg.get("model") or _gc().get("models", {}).get("default")
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

        # --- Apex Analyzer (Salesforce Apex code search/analysis — disabled by default) ---
        # Searches local IDE project for Apex classes, triggers, SOQL, anti-patterns
        apex_cfg = tools_cfg.get("apex_analyzer", {})
        if apex_cfg.get("enabled", False):
            project_root = apex_cfg.get("project_root")  # Optional: override default macOS paths
            self.tools["apex_analyzer"] = ApexAnalyzerTool(project_root=project_root)
            logger.info("Apex analyzer registered (project_root=%s)", project_root or "auto-detect macOS defaults")

        # --- Confluence Crawler (crawl Confluence via CDP + embed into vector store) ---
        conf_cfg = tools_cfg.get("confluence_crawler", {})
        if conf_cfg.get("enabled", False):
            # Pass CDP tool + vector store to crawler
            cdp_tool = self.tools.get("cdp")  # May be None if CDP disabled
            self.tools["confluence_crawler"] = ConfluenceCrawler(cdp_tool=cdp_tool, vector_store=vector_store)
            logger.info("Confluence crawler registered")

        # --- Aura Recon (Salesforce Lightning Aura descriptor discovery — disabled by default) ---
        # Talks to BrowserBox to sniff live /aura XHR traffic and replay working descriptors.
        aura_cfg = tools_cfg.get("aura_recon", {})
        if aura_cfg.get("enabled", False):
            self.tools["aura_recon"] = AuraReconTool(
                ws_url=aura_cfg.get("ws_url", "ws://localhost:9009"),
                timeout=float(aura_cfg.get("timeout", 15.0)),
                default_sniff_seconds=float(aura_cfg.get("default_sniff_seconds", 10.0)),
                state_dir=aura_cfg.get("state_dir"),
            )
            logger.info("Aura recon tool registered (ws_url=%s)", aura_cfg.get("ws_url", "ws://localhost:9009"))

        # --- Atlassian (live Jira + Confluence REST — disabled by default) ---
        # Reads creds from env (ATLASSIAN_BASE_URL/EMAIL/API_TOKEN), set via
        # ~/.beigebox/.env which docker-compose passes through with env_file.
        atl_cfg = tools_cfg.get("atlassian", {})
        if atl_cfg.get("enabled", False):
            self.tools["atlassian"] = AtlassianTool(
                base_url=atl_cfg.get("base_url"),  # optional override; env var wins by default
                email=atl_cfg.get("email"),
                api_token=atl_cfg.get("api_token"),
            )
            logger.info("Atlassian tool registered")

        # --- SF Ingest (Salesforce list-view paging + case fetch + markdown writer — disabled by default) ---
        # Uses BrowserBox + Aura framework. Shares BBClient with aura_recon.
        sfi_cfg = tools_cfg.get("sf_ingest", {})
        if sfi_cfg.get("enabled", False):
            self.tools["sf_ingest"] = SfIngestTool(
                ws_url  = sfi_cfg.get("ws_url", "ws://localhost:9009"),
                timeout = float(sfi_cfg.get("timeout", 120.0)),
                out_dir = sfi_cfg.get("out_dir"),
                internal_org_names = sfi_cfg.get("internal_org_names", []),
            )
            logger.info("SF ingest tool registered (ws_url=%s)", sfi_cfg.get("ws_url", "ws://localhost:9009"))

        # --- Python Interpreter (TIR — disabled by default, requires bwrap) ---
        # Registered under the key "python" (not "python_interpreter") so the
        # operator can call it as {"tool": "python", "input": "..."}.
        pi_cfg = tools_cfg.get("python_interpreter", {})
        if pi_cfg.get("enabled", False):
            self.tools["python"] = PythonInterpreterTool()

        # --- BlueTruth (Bluetooth diagnostic & device simulation — disabled by default) ---
        # Requires bluTruth to be running (e.g., sudo blutruth serve)
        bt_cfg = tools_cfg.get("bluetruth", {})
        if bt_cfg.get("enabled", False):
            db_path = bt_cfg.get("db_path")
            api_url = bt_cfg.get("api_url", "http://localhost:8484")
            self.tools["bluetruth"] = BlueTruthTool(db_path=db_path, api_url=api_url)
            logger.info("BlueTruth tool registered (api_url=%s)", api_url)

        # --- NetworkAudit (local network discovery + port scanning — disabled by default) ---
        # No external binaries required; uses stdlib socket + subprocess ping.
        # Graceful degradation without root (TCP connect scan instead of SYN).
        na_cfg = tools_cfg.get("network_audit", {})
        if na_cfg.get("enabled", False):
            self.tools["network_audit"] = NetworkAuditTool(
                default_timeout=float(na_cfg.get("timeout", 1.0)),
                default_concurrency=int(na_cfg.get("concurrency", 200)),
                max_hosts=int(na_cfg.get("max_hosts", 256)),
            )
            logger.info("NetworkAudit tool registered (timeout=%.1fs, concurrency=%d)",
                        na_cfg.get("timeout", 1.0), na_cfg.get("concurrency", 200))

        # --- API Anomaly Detector (security monitoring — disabled by default) ---
        # Wraps the APIAnomalyDetector from beigebox.security.anomaly_detector.
        # The actual detector is late-bound from Proxy after init.
        aad_cfg = tools_cfg.get("api_anomaly_detector", {})
        sec_cfg = cfg.get("security", {}).get("api_anomaly", {})
        if aad_cfg.get("enabled", False) or sec_cfg.get("enabled", False):
            self.tools["api_anomaly_detector"] = APIAnomalyDetectorTool(detector=None)
            logger.info("APIAnomalyDetectorTool registered (detector late-bound from proxy)")

        # --- Memory Validator (conversation integrity via HMAC-SHA256 — disabled by default) ---
        # Requires security.memory_integrity to be configured with a valid key.
        mv_cfg = tools_cfg.get("memory_validator", {})
        sec_mi_cfg = cfg.get("security", {}).get("memory_integrity", {})
        if mv_cfg.get("enabled", False) or sec_mi_cfg.get("enabled", False):
            from beigebox.security.memory_validator import MemoryValidator
            _mv = MemoryValidator(sec_mi_cfg)
            self.tools["memory_validator"] = MemoryValidatorTool(validator=_mv, store=None)
            logger.info("MemoryValidator tool registered (mode=%s, active=%s)", _mv.mode, _mv.is_active)

        # --- MCP Parameter Validator (P1-B security hardening — disabled by default) ---
        # Multi-tier parameter validation for tool calls: schema, constraint, semantic, isolation.
        # Can be called directly or used as a pre-execution hook in the Operator.
        mcp_val_cfg = cfg.get("security", {}).get("mcp_validator", {})
        if mcp_val_cfg.get("enabled", False):
            self.tools["mcp_parameter_validator"] = MCPValidatorTool(
                allow_unsafe=mcp_val_cfg.get("allow_unsafe", False),
                log_violations=mcp_val_cfg.get("log_violations", True),
            )
            logger.info("MCPValidatorTool registered (allow_unsafe=%s)", mcp_val_cfg.get("allow_unsafe", False))

        # --- Plan Manager (orchestration plan.md management — enabled by default when tools enabled) ---
        pm_cfg = tools_cfg.get("plan_manager", {})
        if pm_cfg.get("enabled", True):
            self.tools["plan_manager"] = PlanManagerTool(workspace_out=_ws_out)
            logger.info("PlanManager tool registered")

        # --- Research Agent (focused research on a subtopic — disabled by default) ---
        ra_cfg = tools_cfg.get("research_agent", {})
        if ra_cfg.get("enabled", False):
            self.tools["research_agent"] = ResearchAgentTool(workspace_out=_ws_out)
            logger.info("ResearchAgent tool registered")

        # --- Research Agent Flexible (backend-agnostic research — disabled by default) ---
        # Accepts "provider:model" at runtime (e.g. "openrouter:arcee/trinity").
        # Falls back to configured default when backend not specified or unavailable.
        raf_cfg = tools_cfg.get("research_agent_flexible", {})
        if raf_cfg.get("enabled", False):
            # Try to pass the existing MultiBackendRouter for the beigebox adapter
            _router = getattr(self, "_router", None)
            self.tools["research_agent_flexible"] = ResearchAgentFlexibleTool(
                workspace_out=_ws_out, router=_router,
            )
            logger.info("ResearchAgentFlexible tool registered")

        # --- Parallel Research (multi-agent concurrent research — disabled by default) ---
        pr_cfg = tools_cfg.get("parallel_research", {})
        if pr_cfg.get("enabled", False):
            self.tools["parallel_research"] = ParallelResearchTool(workspace_out=_ws_out)
            logger.info("ParallelResearch tool registered")

        # --- Evidence Synthesis (cross-finding pattern extraction — disabled by default) ---
        es_cfg = tools_cfg.get("evidence_synthesis", {})
        if es_cfg.get("enabled", False):
            self.tools["evidence_synthesis"] = EvidenceSynthesisTool(workspace_out=_ws_out)
            logger.info("EvidenceSynthesis tool registered")

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

        Steps:
        1. Validate input (parameter validation, injection detection)
        2. Run the tool
        3. Log result and notify webhook

        Returns None if validation fails (mode=strict) or execution fails.
        """
        tool = self.tools.get(name)
        if tool is None:
            logger.warning("Tool '%s' not found in registry", name)
            return None

        # Step 1: Validate input parameters (Phase 1)
        validation_result = self.validator.validate_tool_input(name, input_text)
        if not validation_result.is_valid:
            logger.error(
                "Tool '%s' input validation failed: %s",
                name,
                validation_result.errors,
            )
            return (
                f"Error: input validation failed for '{name}': "
                + "; ".join(validation_result.errors)
            )

        # Use cleaned input if validator provided it
        cleaned_input = validation_result.cleaned_input or input_text

        # Step 2: Execute tool
        start = time.monotonic()
        try:
            result = tool.run(cleaned_input)
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

        # Step 3: Notify and log
        if result is not None:
            self.notifier.notify(name, input_text, result, elapsed_ms)

        # Log tool success
        try:
            log_tool_call(name, "success", latency_ms=elapsed_ms)
        except Exception:
            pass

        return result
