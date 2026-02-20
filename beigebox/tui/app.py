"""
BeigeBox Console — jack into the box.
Textual-based TUI with a phreaker/hacker aesthetic.
Lavender accent, dark terminal palette, extensible screen registry.
Entry point: beigebox jack (alias: console, tui)
"""
from __future__ import annotations
import json
import time
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane
from textual.reactive import reactive
from beigebox.tui.screens.tap import TapScreen
from beigebox.tui.screens.config import ConfigScreen

# ---------------------------------------------------------------------------
# Screen registry — add new screens here to extend the TUI
# Each entry: (key, label, phreaker_name, screen_class)
# ---------------------------------------------------------------------------
SCREEN_REGISTRY: list[tuple[str, str, str, type[Screen]]] = [
    ("1", "Config",  "flash",   ConfigScreen),
    ("2", "Tap",     "tap",     TapScreen),
    # ("3", "Sweep",  "sweep",   SweepScreen),   # future
    # ("4", "Stats",  "ring",    StatsScreen),   # future
    # ("5", "Hooks",  "hooks",   HooksScreen),   # future
]
class BeigeBoxApp(App):
    """BeigeBox hacker TUI."""
    CSS_PATH = str(Path(__file__).parent / "styles" / "main.tcss")
    TITLE = "BeigeBox Console"
    SUB_TITLE = "tap the line · own the conversation"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Disconnect", priority=True),
        Binding("1", "switch_tab('flash')",   "Config",  show=True),
        Binding("2", "switch_tab('tap')",      "Tap",     show=True),
        Binding("r", "refresh_all",            "Refresh", show=True),
    ]
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial=""):
            for _key, label, tab_id, screen_cls in SCREEN_REGISTRY:
                with TabPane(label, id=tab_id):
                    yield screen_cls()
        yield Footer()

    def on_mount(self) -> None:
        """Set initial active tab after compose so IDs are fully registered."""
        try:
            tabs = self.query_one(TabbedContent)
            # Use call_after_refresh to ensure all tab IDs exist in the DOM
            self.call_after_refresh(setattr, tabs, "active", "flash")
        except Exception:
            pass
    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id
    def action_refresh_all(self) -> None:
        for _key, _label, tab_id, _cls in SCREEN_REGISTRY:
            try:
                widget = self.query_one(f"#{tab_id}")
                if hasattr(widget, "refresh_content"):
                    widget.refresh_content()
            except Exception:
                pass
