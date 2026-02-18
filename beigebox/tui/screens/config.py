"""
Config screen — flash panel in TUI form.
Shows live config.yaml values and runtime_config.yaml overrides side-by-side.
Highlights missing/placeholder values in yellow, booleans in appropriate colors.
Runtime overrides that differ from the base config are marked with a ◈ badge.
"""
from __future__ import annotations
import yaml
from pathlib import Path
from typing import Any
from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical, Horizontal
from textual.widgets import Static
from beigebox.tui.screens.base import BeigeBoxPane
from beigebox.config import get_config
# Path to runtime config (relative to repo root, same as everything else)
_RUNTIME_CFG_PATH = Path(__file__).parent.parent.parent.parent / "runtime_config.yaml"
# Placeholder strings that mean "not configured yet"
_PLACEHOLDERS = {
    "your-model-here",
    "your-router-model",
    "your-default-model",
    "your-code-model",
    "your-large-model",
    "your-fast-model",
    "",
}
def _load_runtime_cfg() -> dict:
    """Load runtime_config.yaml. Returns empty dict on failure."""
    try:
        if _RUNTIME_CFG_PATH.exists():
            with open(_RUNTIME_CFG_PATH) as f:
                data = yaml.safe_load(f) or {}
                return data.get("runtime", {})
    except Exception:
        pass
    return {}
def _fmt_val(value: Any) -> tuple[str, str]:
    """
    Return (display_string, css_class) for a config value.
    css_class is one of: config-value, config-value-ok, config-value-warning, config-value-error
    """
    if isinstance(value, bool):
        return (str(value), "config-value-ok" if value else "config-value-warning")
    if isinstance(value, (int, float)):
        return (str(value), "config-value")
    if isinstance(value, str):
        if value in _PLACEHOLDERS:
            return (f'"{value}" ← needs configuration', "config-value-warning")
        return (f'"{value}"', "config-value")
    if isinstance(value, list):
        if not value:
            return ("[]", "config-value-warning")
        return (str(value), "config-value")
    if value is None:
        return ("null", "config-value-warning")
    return (str(value), "config-value")
def _kv_markup(key: str, value: Any, indent: int = 0, runtime_override: Any = None) -> str:
    """Build a Rich markup string for one config key-value row."""
    pad = "  " * indent
    display, cls = _fmt_val(value)
    badge = ""
    if runtime_override is not None and runtime_override != "" and runtime_override != value:
        ro_display, _ = _fmt_val(runtime_override)
        badge = f" [runtime-badge]◈ runtime: {ro_display}[/runtime-badge]"
    return f"{pad}[config-key]{key}[/config-key]  [{cls}]{display}[/{cls}]{badge}"
def _section(title: str) -> str:
    return f"\\n[section-header]── {title} ──[/section-header]"
class ConfigScreen(BeigeBoxPane):
    """Live config viewer with runtime override annotations."""
    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="config-scroll"):
            yield Static(id="config-body", markup=True)
    def on_mount(self) -> None:
        self.refresh_content()
    def refresh_content(self) -> None:
        try:
            cfg = get_config()
            rt = _load_runtime_cfg()
            lines = self._build_markup(cfg, rt)
            self.query_one("#config-body", Static).update("\\n".join(lines))
        except Exception as e:
            self.query_one("#config-body", Static).update(
                f"[config-value-error]Error loading config: {e}[/config-value-error]"
            )
    def _build_markup(self, cfg: dict, rt: dict) -> list[str]:
        lines: list[str] = []
        lines.append("[panel-title]◈ BeigeBox Configuration[/panel-title]")
        lines.append("[dim]config.yaml  ◈ = runtime override active[/dim]")
        # ── Backend ──────────────────────────────────────────────────────────
        lines.append(_section("Backend"))
        b = cfg.get("backend", {})
        lines.append(_kv_markup("url",           b.get("url", ""),           1))
        lines.append(_kv_markup("default_model", b.get("default_model", ""), 1,
                                rt.get("default_model") or None))
        lines.append(_kv_markup("timeout",       b.get("timeout", 120),      1))
        # ── Server ───────────────────────────────────────────────────────────
        lines.append(_section("Middleware Server"))
        s = cfg.get("server", {})
        lines.append(_kv_markup("host", s.get("host", "0.0.0.0"), 1))
        lines.append(_kv_markup("port", s.get("port", 8000),      1))
        # ── Embedding ────────────────────────────────────────────────────────
        lines.append(_section("Embedding"))
        e = cfg.get("embedding", {})
        lines.append(_kv_markup("model",       e.get("model", ""),        1))
        lines.append(_kv_markup("backend_url", e.get("backend_url", ""),  1))
        # ── Storage ──────────────────────────────────────────────────────────
        lines.append(_section("Storage"))
        st = cfg.get("storage", {})
        lines.append(_kv_markup("sqlite_path",       st.get("sqlite_path", ""),      1))
        lines.append(_kv_markup("chroma_path",       st.get("chroma_path", ""),      1))
        lines.append(_kv_markup("log_conversations", st.get("log_conversations", True), 1))
        # ── Decision LLM ─────────────────────────────────────────────────────
        lines.append(_section("Decision LLM"))
        d = cfg.get("decision_llm", {})
        lines.append(_kv_markup("enabled", d.get("enabled", False), 1))
        if d.get("enabled"):
            lines.append(_kv_markup("model",      d.get("model", ""),  1))
            lines.append(_kv_markup("timeout",    d.get("timeout", 5), 1))
            lines.append(_kv_markup("max_tokens", d.get("max_tokens", 256), 1))
        routes = d.get("routes", {})
        if routes:
            lines.append("  [section-header]routes:[/section-header]")
            for route_name, route_cfg in routes.items():
                lines.append(f"  [config-key]{route_name}[/config-key]")
                lines.append(_kv_markup("model",       route_cfg.get("model", ""),       2))
                lines.append(_kv_markup("description", route_cfg.get("description", ""), 2))
        # ── Embedding Classifier ─────────────────────────────────────────────
        lines.append(_section("Embedding Classifier"))
        ec = cfg.get("embedding_classifier", {})
        threshold = ec.get("borderline_threshold", 0.04)
        lines.append(_kv_markup("borderline_threshold", threshold, 1,
                                rt.get("border_threshold") or None))
        lines.append(_kv_markup("agentic_threshold", ec.get("agentic_threshold", 0.5), 1,
                                rt.get("agentic_threshold") or None))
        # ── Tools ────────────────────────────────────────────────────────────
        lines.append(_section("Tools"))
        tools_cfg = cfg.get("tools", {})
        disabled = rt.get("tools_disabled", [])
        lines.append(_kv_markup("enabled", tools_cfg.get("enabled", True), 1))
        tool_names = ["web_search", "web_scraper", "calculator", "datetime", "system_info", "memory"]
        for tool in tool_names:
            t = tools_cfg.get(tool, {})
            is_enabled = t.get("enabled", False)
            rt_disabled = tool in (disabled or [])
            display_class = "config-value-ok" if (is_enabled and not rt_disabled) else "config-value-warning"
            rt_note = " [runtime-badge]◈ disabled this session[/runtime-badge]" if rt_disabled else ""
            lines.append(f"  [config-key]{tool}[/config-key]  [{display_class}]{is_enabled}[/{display_class}]{rt_note}")
        # ── Hooks ────────────────────────────────────────────────────────────
        lines.append(_section("Hooks"))
        h = cfg.get("hooks", {})
        hooks_dir = h.get("directory", "./hooks")
        lines.append(_kv_markup("directory", hooks_dir, 1))
        from pathlib import Path as P
        hp = P(hooks_dir)
        if hp.exists():
            hook_files = sorted([f.stem for f in hp.glob("*.py") if not f.name.startswith("_")])
            if hook_files:
                for hf in hook_files:
                    lines.append(f"    [config-value-ok]✓ {hf}[/config-value-ok]")
            else:
                lines.append("    [dim]no hooks installed[/dim]")
        else:
            lines.append("    [config-value-warning]directory not found[/config-value-warning]")
        # ── Runtime Overrides ────────────────────────────────────────────────
        lines.append(_section("Runtime Overrides  (runtime_config.yaml)"))
        if not rt:
            lines.append("  [dim]no active overrides[/dim]")
        else:
            for k, v in rt.items():
                if v not in (None, "", [], False, 0):
                    lines.append(_kv_markup(k, v, 1))
            # Show force_route if set
            fr = rt.get("force_route", "")
            if fr:
                lines.append(f"  [config-value-error]⚠ force_route={fr!r} — all requests locked to this route[/config-value-error]")
        # ── System prompt prefix ─────────────────────────────────────────────
        sp = rt.get("system_prompt_prefix", "")
        if sp:
            lines.append(_section("Active System Prompt Prefix"))
            lines.append(f"  [config-value]{sp[:200]}[/config-value]")
            if len(sp) > 200:
                lines.append(f"  [dim]... ({len(sp)} chars total)[/dim]")
        lines.append("")  # trailing space
        return lines
