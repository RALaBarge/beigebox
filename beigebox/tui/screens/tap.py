"""
Tap screen — live wire feed in the TUI.
Reads from wire.jsonl and displays entries with role-coded colors.
Auto-refreshes every second when the proxy is running.
Scroll to browse history; newest entries appear at the bottom.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import Static
from textual import work
from textual.timer import Timer
from beigebox.tui.screens.base import BeigeBoxPane
# ── Wire entry rendering ──────────────────────────────────────────────────────
_ROLE_STYLE: dict[str, str] = {
    "user":      "wire-inbound",
    "assistant": "wire-outbound",
    "system":    "wire-system",
    "tool":      "wire-tool",
    "decision":  "wire-decision",
    "error":     "wire-error",
}
_DIR_ARROW: dict[str, str] = {
    "inbound":  "──▶",
    "outbound": "◀──",
    "internal": "─●─",
}
_ROLE_ICON: dict[str, str] = {
    "user":      "▶",
    "assistant": "◀",
    "system":    "●",
    "tool":      "⚡",
    "decision":  "⚖",
    "error":     "✗",
}
def _format_ts(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return ts[:8] if ts else "??:??:??"
def _entry_markup(entry: dict, max_content: int = 300) -> str:
    """Render a wire log entry as Rich markup."""
    ts       = _format_ts(entry.get("ts", ""))
    role     = entry.get("role", "?")
    direction= entry.get("dir", "?")
    model    = entry.get("model", "")
    conv     = entry.get("conv", "")
    content  = entry.get("content", "")
    char_len = entry.get("len", 0)
    tool     = entry.get("tool", "")
    style = _ROLE_STYLE.get(role, "config-value")
    arrow = _DIR_ARROW.get(direction, "───")
    icon  = _ROLE_ICON.get(role, "?")
    # Header line
    model_part = f" [wire-model][{model}][/wire-model]" if model else ""
    tool_part  = f" [wire-tool]⚡{tool}[/wire-tool]" if tool else ""
    conv_part  = f" [wire-timestamp]conv:{conv[:12]}[/wire-timestamp]" if conv else ""
    len_part   = f" [dim]({char_len}c)[/dim]"
    header = (
        f"[wire-timestamp]{ts}[/wire-timestamp] "
        f"[dim]{arrow}[/dim] "
        f"[{style}]{icon} {role.upper()}[/{style}]"
        f"{model_part}{tool_part}{len_part}{conv_part}"
    )
    # Content — clamp and clean
    display = content.replace("[", "\\\\[")  # escape Rich markup in content
    if len(display) > max_content:
        display = display[:max_content] + f" [dim]…({len(content)}c)[/dim]"
    # Indent content lines
    content_lines = display.split("\\n")[:8]
    if display.count("\\n") > 8:
        content_lines.append(f"[dim]  … {display.count(chr(10)) - 8} more lines[/dim]")
    indented = "\\n    ".join(content_lines)
    sep = "[wire-separator]  " + "─" * 58 + "[/wire-separator]"
    return f"{header}\\n    [wire-content]{indented}[/wire-content]\\n{sep}"
# ── Screen ────────────────────────────────────────────────────────────────────
class TapScreen(BeigeBoxPane):
    """Live wire feed — equivalent to `beigebox tap` but interactive."""
    # How many recent entries to display on initial load and refresh
    INITIAL_LINES = 40
    # How often to poll for new entries (seconds)
    POLL_INTERVAL = 1.0
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wire_path: Path | None = None
        self._last_size: int = 0      # byte offset we've read to
        self._entries: list[dict] = []
        self._poller: Timer | None = None
    def compose(self) -> ComposeResult:
        yield Static("", id="tap-status", markup=True)
        with ScrollableContainer(id="tap-scroll"):
            yield Static(id="tap-body", markup=True)
    def on_mount(self) -> None:
        self._wire_path = self._find_wire_path()
        self.refresh_content()
        self._poller = self.set_interval(self.POLL_INTERVAL, self._poll)
    def _find_wire_path(self) -> Path | None:
        try:
            from beigebox.config import get_config
            cfg = get_config()
            p = Path(cfg.get("wiretap", {}).get("path", "./data/wire.jsonl"))
            return p
        except Exception:
            return Path("./data/wire.jsonl")
    def _read_entries(self) -> list[dict]:
        """Read all entries from wire.jsonl."""
        if not self._wire_path or not self._wire_path.exists():
            return []
        entries = []
        try:
            with open(self._wire_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        return entries
    def _poll(self) -> None:
        """Called every POLL_INTERVAL — check for new wire entries."""
        if not self._wire_path or not self._wire_path.exists():
            return
        try:
            current_size = self._wire_path.stat().st_size
        except OSError:
            return
        if current_size == self._last_size:
            return
        self._last_size = current_size
        self.refresh_content()
    def refresh_content(self) -> None:
        """Reload and re-render the wire log."""
        self._entries = self._read_entries()
        self._render()
    def _render(self) -> None:
        entries = self._entries[-self.INITIAL_LINES:]  # show most recent N
        body = self.query_one("#tap-body", Static)
        status = self.query_one("#tap-status", Static)
        if not self._wire_path or not self._wire_path.exists():
            body.update(
                "[wire-error]✗ No wire log found.[/wire-error]\\n"
                "[dim]  Start BeigeBox first: [/dim][config-key]beigebox dial[/config-key]"
            )
            status.update("[dim]── offline ──[/dim]")
            return
        total = len(self._entries)
        shown = len(entries)
        status.update(
            f"[dim]wire: {self._wire_path}  │  "
            f"{total} total entries  │  showing last {shown}  │  "
            f"polling every {self.POLL_INTERVAL:.0f}s[/dim]"
        )
        if not entries:
            body.update(
                "[dim]No traffic yet. Send a message through BeigeBox to see the wire.[/dim]"
            )
            return
        rendered = []
        for entry in entries:
            try:
                rendered.append(_entry_markup(entry))
            except Exception as exc:
                rendered.append(f"[wire-error]render error: {exc}[/wire-error]")
        body.update("\\n".join(rendered))
        # Auto-scroll to bottom so new entries are visible
        scroll = self.query_one("#tap-scroll", ScrollableContainer)
        scroll.scroll_end(animate=False)
