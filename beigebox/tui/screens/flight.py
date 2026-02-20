"""
Flight Recorder screen — live request timeline viewer in TUI form.
Polls the in-memory FlightRecorderStore and renders recent records
with per-stage timing bars and color-coded latency indicators.
"""
from __future__ import annotations
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import Static
from textual.timer import Timer
from beigebox.tui.screens.base import BeigeBoxPane

_LATENCY_THRESHOLDS = [
    (100,  "flight-fast"),    # <100ms  green
    (500,  "flight-medium"),  # <500ms  yellow
    (2000, "flight-slow"),    # <2s     orange
]

def _latency_class(ms: float) -> str:
    for threshold, cls in _LATENCY_THRESHOLDS:
        if ms < threshold:
            return cls
    return "flight-critical"

def _bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)

def _render_record(rec) -> str:
    lines = []
    total = rec.total_ms
    lat_cls = _latency_class(total)

    # Header
    model_part = f" [flight-model][{rec.model}][/flight-model]" if rec.model else ""
    conv_part  = f" [dim]conv:{rec.conversation_id[:12]}[/dim]" if rec.conversation_id else ""
    lines.append(
        f"[flight-id]◈ {rec.id}[/flight-id]{model_part}{conv_part}  "
        f"[{lat_cls}]{total:.0f}ms[/{lat_cls}]"
    )

    # Events
    for i, event in enumerate(rec.events):
        elapsed = event["elapsed_ms"]
        stage   = event["stage"]
        details = event.get("details", {})
        detail_str = ""
        if details:
            detail_str = "  [dim](" + ", ".join(f"{k}={v}" for k, v in details.items()) + ")[/dim]"
        lines.append(f"  [dim]{elapsed:8.1f}ms[/dim]  [flight-stage]{stage}[/flight-stage]{detail_str}")

    # Breakdown bars
    summary = rec.summary()
    breakdown = summary.get("breakdown", {})
    if breakdown:
        lines.append("")
        for stage, info in list(breakdown.items())[:5]:
            bar = _bar(info["pct"])
            cls = _latency_class(info["ms"])
            lines.append(
                f"  [dim]{stage:<28}[/dim] [{cls}]{bar}[/{cls}]  "
                f"[dim]{info['ms']:.0f}ms ({info['pct']}%)[/dim]"
            )

    lines.append("[flight-separator]  " + "─" * 60 + "[/flight-separator]")
    return "\n".join(lines)


class FlightScreen(BeigeBoxPane):
    """Live flight recorder — shows recent request timelines."""

    POLL_INTERVAL = 2.0
    MAX_RECORDS   = 15

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._store = None
        self._poller: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="flight-status", markup=True)
        with ScrollableContainer(id="flight-scroll"):
            yield Static(id="flight-body", markup=True)

    def on_mount(self) -> None:
        self._load_store()
        self.refresh_content()
        self._poller = self.set_interval(self.POLL_INTERVAL, self.refresh_content)

    def _load_store(self) -> None:
        try:
            from beigebox.main import flight_recorder
            self._store = flight_recorder
        except Exception:
            self._store = None

    def refresh_content(self) -> None:
        status  = self.query_one("#flight-status", Static)
        body    = self.query_one("#flight-body",   Static)

        if self._store is None:
            self._load_store()

        if self._store is None:
            status.update("[dim]── flight recorder offline ──[/dim]")
            body.update(
                "[flight-separator]No flight recorder available.[/flight-separator]\n"
                "[dim]Start BeigeBox and enable flight_recorder in config.yaml[/dim]"
            )
            return

        records = self._store.recent(self.MAX_RECORDS)
        count   = self._store.count

        status.update(
            f"[dim]flight recorder  │  {count} total records  │  "
            f"showing last {min(len(records), self.MAX_RECORDS)}  │  "
            f"polling every {self.POLL_INTERVAL:.0f}s[/dim]"
        )

        if not records:
            body.update("[dim]No flight records yet. Send a request through BeigeBox.[/dim]")
            return

        rendered = []
        for rec in reversed(records):  # newest first
            try:
                rendered.append(_render_record(rec))
            except Exception as exc:
                rendered.append(f"[flight-separator]render error: {exc}[/flight-separator]")

        body.update("\n".join(rendered))
        scroll = self.query_one("#flight-scroll", ScrollableContainer)
        scroll.scroll_home(animate=False)
