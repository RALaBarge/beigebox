"""
Base class for all BeigeBox TUI screens.
All content panes inherit from BeigeBoxPane, which provides:
  - refresh_content() hook (called by app-level refresh action)
  - standard compose helpers for section headers, key-value rows
"""
from __future__ import annotations
from textual.widget import Widget
from textual.widgets import Static
class BeigeBoxPane(Widget):
    """
    Base widget for all TUI panel content.
    Subclass this, implement compose() and optionally refresh_content().
    The app calls refresh_content() on all panes when 'r' is pressed.
    """
    DEFAULT_CSS = """
    BeigeBoxPane {
        height: 1fr;
        width: 1fr;
    }
    """
    def refresh_content(self) -> None:
        """Called by the app to request a data refresh. Override in subclasses."""
        self.refresh()
    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def kv(key: str, value: str, value_class: str = "config-value") -> str:
        """Format a key-value pair as Rich markup."""
        return f"[@click]{key}[/]  [{value_class}]{value}[/{value_class}]"
    @staticmethod
    def section(title: str) -> Static:
        """Return a styled section header widget."""
        return Static(f"[bold green]── {title} ──[/bold green]", markup=True)
