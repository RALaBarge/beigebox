"""
Wiretap — listen in on the wire.

Two parts:
  1. WireLog: writes structured JSONL entries for every message through the proxy
  2. live_tap(): reads the JSONL and renders a fancy color-coded live view

The wire log is separate from the debug log. It's a clean, structured record
of exactly what went over the line — who said what, when, to which model.
"""

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
}

ROLE_ICONS = {
    "user": "▶",
    "assistant": "◀",
    "system": "●",
    "tool": "⚡",
}


class WireLog:
    """
    Structured JSONL logger for the wire.
    Each line is one event: a message going through the proxy.
    
    Format:
        {"ts": "...", "dir": "inbound|outbound", "role": "...", 
         "model": "...", "conv": "...", "len": 123, "content": "..."}
    """

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = None

    def _ensure_open(self):
        if self._file is None:
            self._file = open(self.log_path, "a", buffering=1)  # line-buffered

    def log(
        self,
        direction: str,  # "inbound" (user->proxy) or "outbound" (proxy->user)
        role: str,
        content: str,
        model: str = "",
        conversation_id: str = "",
        token_count: int = 0,
        tool_name: str = "",
    ):
        """Write a wire log entry."""
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

        # Store content — truncate for sanity but keep full for short messages
        if len(content) <= 2000:
            entry["content"] = content
        else:
            entry["content"] = content[:1000] + f"\n\n[... {len(content) - 2000} chars truncated ...]\n\n" + content[-1000:]

        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
            # Seek to end
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
