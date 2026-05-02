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

from beigebox.storage.wire_sink import JsonlWireSink, SqliteWireSink, WireSink

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

    Extra WireSink instances can be injected via the `sinks` parameter for
    additional fanout (e.g. remote observability sinks).
    """

    def __init__(
        self,
        log_path: str,
        wire_events=None,
        egress_hooks=None,
        max_lines: int = 100_000,
        rotation_enabled: bool = True,
        sinks: list[WireSink] | None = None,
    ):
        self.log_path = Path(log_path)
        self._egress = egress_hooks or []  # list[EgressHook] — fire-and-forget

        # Primary JSONL sink — always active
        self._jsonl_sink = JsonlWireSink(
            path=log_path,
            max_lines=max_lines,
            rotation_enabled=rotation_enabled,
        )

        # Optional extra sinks (SQLite dual-write via the wire_events repo
        # plus any caller-supplied sinks).
        self._extra_sinks: list[WireSink] = list(sinks or [])
        if wire_events is not None:
            self._extra_sinks.append(SqliteWireSink(wire_events))

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

        self._jsonl_sink.write(entry)

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

        # Fan out to extra sinks (SQLite dual-write and any injected sinks)
        if self._extra_sinks:
            _meta: dict = meta.copy() if meta else {}
            if latency_ms is not None:
                _meta["latency_ms"] = round(latency_ms, 1)
            if timing:
                _meta["timing"] = {k: round(v, 1) for k, v in timing.items()}
            if token_count:
                _meta["tokens"] = token_count
            if tool_name:
                _meta["tool_name"] = tool_name
            sqlite_event = {
                "event_type": event_type,
                "source": source,
                "content": entry.get("content", ""),
                "role": role,
                "model": model,
                "conv_id": conversation_id or None,
                "run_id": run_id,
                "turn_id": turn_id,
                "tool_id": tool_id,
                "meta": _meta if _meta else None,
            }
            # Per-sink try/except so one failing sink doesn't drop the
            # others. PostgresWireSink can fail when the network blips
            # or the postgres container restarts; we don't want that to
            # take JSONL + SQLite down with it.
            for sink in self._extra_sinks:
                try:
                    sink.write(sqlite_event)
                except Exception as exc:
                    logger.warning(
                        "WireLog sink %s.write failed: %s",
                        type(sink).__name__, exc,
                    )

    def add_sink(self, sink: WireSink) -> None:
        """Attach an additional sink after WireLog has been constructed.

        Used by main.py lifespan to bolt on the PostgresWireSink alongside
        the JSONL + SQLite sinks already attached at __init__ time. Called
        once at startup; not thread-safe for concurrent attaches (which
        we don't need).
        """
        self._extra_sinks.append(sink)

    def write_request(self, req) -> None:
        """Emit one ``model_request_normalized`` event for a captured request.

        Takes a :class:`beigebox.capture.CapturedRequest`. Carries the full
        normalizer audit trail (``target``, ``transforms``, ``errors``,
        ``has_tools``, ``stream``, request ``messages``) in ``meta`` so
        downstream consumers (tap UI, replay, query tools) can reconstruct
        the outgoing call without re-reading proxy state.

        The fanout (JSONL + SqliteWireSink + caller-supplied sinks) is the
        same path :meth:`log` uses; only the meta payload is enriched.
        """
        ctx = req.ctx
        meta = {
            "target": req.target,
            "transforms": list(req.transforms),
            "errors": list(req.errors),
            "has_tools": req.has_tools,
            "stream": req.stream,
            "message_count": len(req.messages),
            "request_messages": req.messages,
            "backend": ctx.backend,
            "started_at": ctx.started_at.isoformat() if ctx.started_at else None,
            "request_id": ctx.request_id,
        }
        # Use the last user message as the displayed content (parity with the
        # old `_log_messages` JSONL output) so live `tap` still shows what
        # the user said. Full message list lives in meta.
        last_user = ""
        for m in reversed(req.messages):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str):
                    last_user = c
                    break
        self.log(
            direction="inbound",
            role="user",
            content=last_user,
            model=ctx.model,
            conversation_id=ctx.conv_id,
            event_type="model_request_normalized",
            source="proxy",
            run_id=ctx.run_id,
            turn_id=ctx.turn_id,
            meta=meta,
        )

    def write_response(self, resp) -> None:
        """Emit one ``model_response_normalized`` event for a captured response.

        Takes a :class:`beigebox.capture.CapturedResponse`. Always emits a
        row, even when ``resp.outcome != "ok"`` — failures, aborts, and
        client disconnects each carry their full partial state in ``meta``
        (``outcome``, ``error_kind``, ``error_message`` plus whatever
        reasoning / tool_calls / usage was assembled before the abort).
        """
        ctx = resp.ctx
        meta: dict = {
            "outcome": resp.outcome,
            "error_kind": resp.error_kind,
            "error_message": resp.error_message,
            "finish_reason": resp.finish_reason,
            "has_reasoning": bool(resp.reasoning),
            "reasoning": resp.reasoning,
            "tool_calls_count": len(resp.tool_calls) if resp.tool_calls else 0,
            "tool_calls": resp.tool_calls,
            "errors": list(resp.response_errors),
            "usage": {
                "prompt_tokens": resp.prompt_tokens,
                "completion_tokens": resp.completion_tokens,
                "reasoning_tokens": resp.reasoning_tokens,
                "total_tokens": resp.total_tokens,
            },
            "cost_usd": resp.cost_usd,
            "backend": ctx.backend,
            "ttft_ms": ctx.ttft_ms,
            "request_id": ctx.request_id,
        }
        self.log(
            direction="outbound",
            role=resp.role or "assistant",
            content=resp.content,
            model=ctx.model,
            conversation_id=ctx.conv_id,
            event_type="model_response_normalized",
            source="proxy",
            run_id=ctx.run_id,
            turn_id=ctx.turn_id,
            latency_ms=ctx.latency_ms,
            meta=meta,
        )

    def close(self):
        self._jsonl_sink.close()
        for sink in self._extra_sinks:
            sink.close()


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
