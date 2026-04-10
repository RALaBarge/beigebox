"""
Wiretap — listen in on the wire.

Two parts:
  1. WireLog: writes structured JSONL entries for every message through the proxy
  2. live_tap(): reads the JSONL and renders a fancy color-coded live view

The wire log is separate from the debug log. It's a clean, structured record
of exactly what went over the line — who said what, when, to which model.
"""

import asyncio
import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ANSI colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_USER = "\033[96m"      # cyan
C_ASSISTANT = "\033[93m"  # yellow
C_SYSTEM = "\033[90m"     # gray
C_MODEL = "\033[95m"      # magenta
C_TIME = "\033[90m"       # gray
C_BORDER = "\033[90m"     # gray
C_TOOL = "\033[92m"       # green
C_ERROR = "\033[91m"      # red


ROLE_COLORS = {
    "user": C_USER,
    "assistant": C_ASSISTANT,
    "system": C_SYSTEM,
    "tool": C_TOOL,
    "decision": C_MODEL,
    "cache": "\033[36m",
    "router": "\033[95m",
    "request": "\033[94m",
    "backend": "\033[33m",
    "classifier": "\033[35m",
    "judge": "\033[33m",
    "harness": "\033[92m",
    "profiler": "\033[90m",
    "error": "\033[91m",
    "proxy": "\033[90m",
    "cost_tracker": "\033[33m",
    "token_counter": "\033[90m",
    "model_selector": "\033[95m",
    "payload": "\033[90m",
    "validation": "\033[91m",
    "guardrails": "\033[91m",
    "wasm": "\033[36m",
}

ROLE_ICONS = {
    "user": "▶",
    "assistant": "◀",
    "system": "●",
    "tool": "⚡",
    "decision": "⚖",
    "cache": "💾",
    "router": "🔀",
    "request": "📡",
    "backend": "🖥",
    "classifier": "🏷",
    "judge": "⚖",
    "harness": "🔄",
    "profiler": "📊",
    "error": "❌",
    "proxy": "🔌",
    "cost_tracker": "💰",
    "token_counter": "🔢",
    "model_selector": "🎯",
    "payload": "📦",
    "validation": "🛡",
    "guardrails": "🚧",
    "wasm": "🧩",
}


class WireLog:
    """
    Structured JSONL logger for the wire.
    Each line is one event: a message going through the proxy.

    Format:
        {"ts": "...", "dir": "inbound|outbound", "role": "...",
         "model": "...", "conv": "...", "len": 123, "content": "..."}

    If sqlite_store is provided, every log() call also writes a structured row
    to the wire_events table so the web UI can cross-link by conv_id / run_id.
    """

    def __init__(self, log_path: str, sqlite_store=None, egress_hooks=None,
                 max_lines: int = 100_000, rotation_enabled: bool = True):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._db = sqlite_store        # optional SQLiteStore for dual-write
        self._egress = egress_hooks or []  # list[EgressHook] — fire-and-forget
        self._max_lines = max_lines
        self._rotation_enabled = rotation_enabled
        self._line_count = 0
        self._line_count_loaded = False

    def _ensure_open(self):
        if self._file is None:
            # buffering=1 = line-buffered: each log() call flushes immediately
            # so the tap viewer sees entries in real time without extra flushing.
            self._file = open(self.log_path, "a", buffering=1)
            if not self._line_count_loaded:
                try:
                    self._line_count = sum(1 for _ in open(self.log_path))
                except (FileNotFoundError, OSError):
                    self._line_count = 0
                self._line_count_loaded = True

    def _rotate_if_needed(self):
        """Rotate JSONL when max_lines exceeded: rename current to .1, start fresh."""
        if not self._rotation_enabled or self._line_count < self._max_lines:
            return
        if self._file:
            self._file.close()
            self._file = None
        rotated = self.log_path.with_suffix(".jsonl.1")
        if rotated.exists():
            rotated.unlink()
        self.log_path.rename(rotated)
        self._line_count = 0
        self._ensure_open()

    def log(
        self,
        direction: str,  # "inbound" (user->proxy) or "outbound" (proxy->user)
        role: str,
        content: str,
        model: str = "",
        conversation_id: str = "",
        token_count: int = 0,
        tool_name: str = "",
        latency_ms: float | None = None,
        timing: dict | None = None,
        # Structured fields for SQLite — do not affect JSONL output
        event_type: str = "message",
        source: str = "proxy",
        run_id: str | None = None,
        turn_id: str | None = None,
        tool_id: str | None = None,
        meta: dict | None = None,
    ):
        """Write a wire log entry.

        Optional timing fields (used for request lifecycle tracking):
            latency_ms: total end-to-end latency in milliseconds
            timing: dict of {stage_name: ms} for per-stage breakdown
        """
        self._ensure_open()
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "dir": direction,
            "role": role,
            "model": model,
            "conv": conversation_id[:16] if conversation_id else "",
            "len": len(content),
            "tokens": token_count,
        }
        if tool_name:
            entry["tool"] = tool_name
        if latency_ms is not None:
            entry["latency_ms"] = round(latency_ms, 1)
        if timing:
            entry["timing"] = {k: round(v, 1) for k, v in timing.items()}
        # Structured fields — parity with SQLite so CLI tap is equally rich
        if event_type != "message":
            entry["event_type"] = event_type
        if source != "proxy":
            entry["source"] = source
        if meta:
            entry["meta"] = meta
        if run_id:
            entry["run_id"] = run_id
        if turn_id:
            entry["turn_id"] = turn_id
        if tool_id:
            entry["tool_id"] = tool_id

        # Preserve head + tail for long messages so the log never blows up
        # while still capturing both the opening context and the conclusion.
        if len(content) <= 2000:
            entry["content"] = content
        else:
            entry["content"] = content[:1000] + f"\n\n[... {len(content) - 2000} chars truncated ...]\n\n" + content[-1000:]

        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._line_count += 1
        self._rotate_if_needed()

        # Fire-and-forget to observability egress hooks (non-blocking)
        if self._egress:
            _egress_event = dict(entry)
            # Preserve full content in the egress payload (not truncated for display)
            _egress_event["content"] = content
            try:
                loop = asyncio.get_running_loop()
                from beigebox.observability.egress import emit_all as _emit_all
                loop.create_task(_emit_all(self._egress, _egress_event))
            except RuntimeError:
                pass  # No running event loop (e.g. during tests) — skip silently

        # Dual-write to SQLite for web UI cross-linking
        if self._db is not None:
            _meta: dict = meta.copy() if meta else {}
            if latency_ms is not None:
                _meta["latency_ms"] = round(latency_ms, 1)
            if timing:
                _meta["timing"] = {k: round(v, 1) for k, v in timing.items()}
            if token_count:
                _meta["tokens"] = token_count
            if tool_name:
                _meta["tool_name"] = tool_name
            self._db.log_wire_event(
                event_type=event_type,
                source=source,
                content=entry.get("content", ""),
                role=role,
                model=model,
                conv_id=conversation_id or None,
                run_id=run_id,
                turn_id=turn_id,
                tool_id=tool_id,
                meta=_meta if _meta else None,
            )

    def close(self):
        if self._file:
            self._file.close()
            self._file = None


def _format_entry(entry: dict, raw: bool = False) -> str:
    """Format a single wire log entry for display."""
    if raw:
        return json.dumps(entry, ensure_ascii=False)

    ts = entry.get("ts", "")
    # Parse and format timestamp nicely
    try:
        dt = datetime.fromisoformat(ts)
        time_str = dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        time_str = ts[:8] if ts else "??:??:??"

    role = entry.get("role", "?")
    direction = entry.get("dir", "?")
    model = entry.get("model", "")
    conv = entry.get("conv", "")
    content = entry.get("content", "")
    char_len = entry.get("len", 0)
    tool = entry.get("tool", "")

    role_color = ROLE_COLORS.get(role, C_RESET)
    icon = ROLE_ICONS.get(role, "?")

    # Direction arrow
    if direction == "inbound":
        arrow = f"{C_DIM}──▶{C_RESET}"
    elif direction == "internal":
        arrow = f"{C_DIM}─●─{C_RESET}"
    else:
        arrow = f"{C_DIM}◀──{C_RESET}"

    # Header line
    lines = []
    header = f"  {C_TIME}{time_str}{C_RESET} {arrow} {role_color}{C_BOLD}{icon} {role.upper()}{C_RESET}"
    if model:
        header += f"  {C_MODEL}[{model}]{C_RESET}"
    if tool:
        header += f"  {C_TOOL}⚡{tool}{C_RESET}"
    header += f"  {C_DIM}({char_len} chars){C_RESET}"
    if conv:
        header += f"  {C_DIM}conv:{conv}{C_RESET}"
    lines.append(header)

    # Content — indent and truncate for display
    if content:
        display_content = content
        if len(display_content) > 500:
            display_content = display_content[:500] + f"\n      {C_DIM}[... truncated]{C_RESET}"

        for cline in display_content.split("\n")[:15]:
            lines.append(f"      {cline}")

        if display_content.count("\n") > 15:
            lines.append(f"      {C_DIM}[... {display_content.count(chr(10)) - 15} more lines]{C_RESET}")

    # Separator
    lines.append(f"  {C_BORDER}{'─' * 60}{C_RESET}")

    return "\n".join(lines)


def live_tap(
    log_path: str | None = None,
    follow: bool = True,
    last_n: int = 20,
    role_filter: str | None = None,
    raw: bool = False,
):
    """
    Live tail of the wire log. Like tcpdump for LLM conversations.
    
    Args:
        log_path: Path to wire.jsonl. If None, reads from config.
        follow: If True, keep watching for new entries (tail -f behavior).
        last_n: Show this many recent entries before following.
        role_filter: Only show entries matching this role.
        raw: Output raw JSONL instead of formatted.
    """
    if log_path is None:
        from beigebox.config import get_config
        cfg = get_config()
        log_path = cfg.get("wiretap", {}).get("path", "./data/wire.jsonl")

    wire_path = Path(log_path)

    if not wire_path.exists():
        print(f"  ✗  No wire log found at {wire_path}")
        print(f"     Start BeigeBox first: beigebox dial")
        return

    if not raw:
        print(f"  ☎  Tapping into {wire_path}")
        print(f"  {C_BORDER}{'═' * 60}{C_RESET}")

    # Read existing entries (show last N)
    all_lines = []
    with open(wire_path) as f:
        all_lines = f.readlines()

    # Show last N entries
    start = max(0, len(all_lines) - last_n)
    for line in all_lines[start:]:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if role_filter and entry.get("role") != role_filter:
                continue
            print(_format_entry(entry, raw=raw))
        except json.JSONDecodeError:
            continue

    if not follow:
        return

    if not raw:
        print(f"\n  {C_DIM}[listening for new traffic... Ctrl+C to hang up]{C_RESET}\n")

    # Follow mode — watch for new lines
    try:
        with open(wire_path) as f:
            # SEEK_END: position at the end of the file before entering the
            # readline loop so we only emit entries written after this call.
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if role_filter and entry.get("role") != role_filter:
                        continue
                    print(_format_entry(entry, raw=raw))
                except json.JSONDecodeError:
                    continue
    except KeyboardInterrupt:
        if not raw:
            print(f"\n  {C_DIM}[line disconnected]{C_RESET}")
