"""
Conversation Replay — reconstruct conversations with full routing context.

Pulls message data from SQLite and correlates with wiretap log entries
to show which model was used for each message, why it was routed that way,
and what tools were invoked.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from beigebox.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class ConversationReplayer:
    """Reconstruct a conversation with routing decisions and tool usage."""

    def __init__(self, sqlite: SQLiteStore, wiretap_path: str = "./data/wire.jsonl"):
        self.sqlite = sqlite
        self.wiretap_path = Path(wiretap_path)

    def replay(self, conversation_id: str) -> dict:
        """
        Reconstruct a full conversation with routing context.

        Returns:
            {
                "conversation_id": "...",
                "timeline": [...],
                "stats": {...},
                "text": "CONVERSATION REPLAY: ..."
            }
        """
        # Get messages from SQLite
        messages = self.sqlite.get_conversation(conversation_id)
        if not messages:
            return {
                "conversation_id": conversation_id,
                "error": "Conversation not found",
                "timeline": [],
                "stats": {},
            }

        # Get routing decisions from wiretap (best-effort)
        wire_events = self._load_wire_events(conversation_id)

        # Build timeline: match messages with routing context
        timeline = []
        for msg in messages:
            routing = self._find_routing_for_message(msg, wire_events)
            tools = self._find_tools_for_message(msg, wire_events)
            backend = self._find_backend_for_message(msg, wire_events)

            entry = {
                "role": msg["role"],
                "content": msg["content"][:500],  # Truncate for API response
                "content_length": len(msg["content"]),
                "model": msg["model"],
                "token_count": msg.get("token_count", 0),
                "cost_usd": msg.get("cost_usd"),
                "timestamp": msg["timestamp"],
                "routing": routing,
                "tools": tools,
                "backend": backend,
            }
            timeline.append(entry)

        stats = self._compute_stats(timeline)

        return {
            "conversation_id": conversation_id,
            "timeline": timeline,
            "stats": stats,
            "text": self._render_text(conversation_id, timeline, stats),
        }

    def _load_wire_events(self, conversation_id: str) -> list[dict]:
        """Load wiretap entries related to this conversation."""
        if not self.wiretap_path.exists():
            return []

        conv_prefix = conversation_id[:16]
        events = []
        try:
            with open(self.wiretap_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        # Include entries that match this conversation
                        # or internal routing/tool entries near the same timestamps
                        entry_conv = entry.get("conv", "")
                        if entry_conv == conv_prefix or (
                            entry.get("dir") == "internal"
                            and entry.get("role") in ("decision", "tool", "system")
                        ):
                            events.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning("Failed to load wiretap for replay: %s", e)

        return events

    def _find_routing_for_message(self, msg: dict, wire_events: list[dict]) -> dict:
        """Find the routing decision that preceded this message."""
        msg_ts = msg.get("timestamp", "")
        if not msg_ts or msg["role"] != "assistant":
            return {}

        # Look for the most recent decision event before this message
        best = None
        for event in wire_events:
            if event.get("role") != "decision":
                continue
            event_ts = event.get("ts", "")
            if event_ts <= msg_ts:
                best = event

        if not best:
            return {}

        content = best.get("content", "")
        method = "unknown"
        confidence = None

        if "session cache hit" in content:
            method = "session_cache"
            confidence = 1.0
        elif "z-command" in content:
            method = "z_command"
            confidence = 1.0
        elif "embedding:" in content:
            method = "embedding_classifier"
            # Try to extract confidence
            for part in content.split():
                if part.startswith("confidence="):
                    try:
                        confidence = float(part.split("=")[1])
                    except (ValueError, IndexError):
                        pass
        elif "route=" in content:
            method = "decision_llm"
        elif "agentic_scorer" in content:
            method = "agentic_scorer"

        return {
            "method": method,
            "confidence": confidence,
            "raw": content[:200],
        }

    def _find_tools_for_message(self, msg: dict, wire_events: list[dict]) -> list[str]:
        """Find tools invoked around this message's timestamp."""
        msg_ts = msg.get("timestamp", "")
        if not msg_ts:
            return []

        tools = []
        for event in wire_events:
            if event.get("role") != "tool":
                continue
            event_ts = event.get("ts", "")
            # Match tools within a reasonable time window
            if event_ts and event_ts <= msg_ts:
                content = event.get("content", "")
                tool_name = event.get("tool", "")
                if tool_name:
                    tools.append(tool_name)
                elif "web_search" in content:
                    tools.append("web_search")
                elif "memory" in content or "RAG" in content:
                    tools.append("memory")

        return list(set(tools))  # Deduplicate

    def _find_backend_for_message(self, msg: dict, wire_events: list[dict]) -> str | None:
        """Find which backend served this message."""
        msg_ts = msg.get("timestamp", "")
        if not msg_ts or msg["role"] != "assistant":
            return None

        for event in reversed(wire_events):
            if event.get("role") != "system":
                continue
            content = event.get("content", "")
            if "routed to backend" in content:
                event_ts = event.get("ts", "")
                if event_ts and event_ts <= msg_ts:
                    # Extract backend name
                    try:
                        start = content.index("'") + 1
                        end = content.index("'", start)
                        return content[start:end]
                    except ValueError:
                        pass
        return None

    def _compute_stats(self, timeline: list[dict]) -> dict:
        """Compute aggregate stats for the conversation."""
        models: dict[str, int] = {}
        routing_methods: dict[str, int] = {}
        tools_used: dict[str, int] = {}
        total_tokens = 0
        total_cost = 0.0
        message_count = len(timeline)

        for entry in timeline:
            model = entry.get("model", "")
            if model:
                models[model] = models.get(model, 0) + 1

            total_tokens += entry.get("token_count", 0)

            cost = entry.get("cost_usd")
            if cost:
                total_cost += cost

            routing = entry.get("routing", {})
            method = routing.get("method", "")
            if method:
                routing_methods[method] = routing_methods.get(method, 0) + 1

            for tool in entry.get("tools", []):
                tools_used[tool] = tools_used.get(tool, 0) + 1

        # Duration
        timestamps = [e["timestamp"] for e in timeline if e.get("timestamp")]
        duration_str = ""
        if len(timestamps) >= 2:
            try:
                first = datetime.fromisoformat(timestamps[0])
                last = datetime.fromisoformat(timestamps[-1])
                delta = last - first
                minutes = delta.total_seconds() / 60
                duration_str = f"{minutes:.1f} minutes"
            except (ValueError, TypeError):
                pass

        return {
            "message_count": message_count,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6) if total_cost else None,
            "duration": duration_str,
            "models": models,
            "routing_methods": routing_methods,
            "tools_used": tools_used,
        }

    def _render_text(self, conversation_id: str, timeline: list[dict], stats: dict) -> str:
        """Render a human-readable replay."""
        lines = [
            f"CONVERSATION REPLAY: {conversation_id[:16]}",
            f"Messages: {stats['message_count']} | "
            f"Tokens: {stats['total_tokens']} | "
            f"Duration: {stats.get('duration', 'N/A')}",
        ]
        if stats.get("total_cost_usd"):
            lines.append(f"Total Cost: ${stats['total_cost_usd']:.6f}")
        lines.append("─" * 60)
        lines.append("")

        for i, entry in enumerate(timeline, 1):
            role = entry["role"].upper()
            content_preview = entry["content"][:80].replace("\n", " ")
            model = entry.get("model", "")
            lines.append(f"  [{i}] {role}: \"{content_preview}\"")

            if model:
                lines.append(f"       Model: {model}")

            routing = entry.get("routing", {})
            if routing:
                method = routing.get("method", "")
                conf = routing.get("confidence")
                conf_str = f" ({conf:.2f})" if conf is not None else ""
                lines.append(f"       Routing: {method}{conf_str}")

            backend = entry.get("backend")
            if backend:
                lines.append(f"       Backend: {backend}")

            tools = entry.get("tools", [])
            if tools:
                lines.append(f"       Tools: {', '.join(tools)}")

            cost = entry.get("cost_usd")
            if cost:
                lines.append(f"       Cost: ${cost:.6f}")

            lines.append("")

        lines.append("─" * 60)
        lines.append("STATS:")
        lines.append(f"  Models: {stats.get('models', {})}")
        lines.append(f"  Routing: {stats.get('routing_methods', {})}")
        if stats.get("tools_used"):
            lines.append(f"  Tools: {stats['tools_used']}")

        return "\n".join(lines)
