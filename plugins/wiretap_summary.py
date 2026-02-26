"""
Wiretap summary plugin.

Lets the LLM surface a human-readable summary of recent proxy traffic —
how many requests, which models, any errors — without the operator needing
to open the Tap tab.

This is a good example of a "introspection" plugin that reads BeigeBox
internals rather than calling external services.

Examples the LLM would route here:
  "What's been going through the proxy lately?"
  "Any recent errors?"
  "Show me the last few requests"
  "What models have been used today?"

Enable in config.yaml:
    tools:
      plugins:
        enabled: true
        wiretap_summary:
          enabled: true
"""

import json
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_NAME = "wiretap_summary"


class WiretapSummaryTool:
    """Summarises recent proxy traffic from wire.jsonl."""

    def run(self, query: str = "") -> str:
        try:
            from beigebox.config import get_config
            cfg = get_config()
            wire_path = Path(cfg.get("wiretap", {}).get("path", "./data/wire.jsonl"))
        except Exception:
            wire_path = Path("./data/wire.jsonl")

        if not wire_path.exists():
            return "No wiretap data found. Is the proxy running and wiretap enabled?"

        entries = []
        try:
            with open(wire_path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                chunk = min(size, 200 * 500)   # ~500 bytes/entry, last 200
                fh.seek(-chunk, 2)
                raw = fh.read().decode("utf-8", errors="replace")
            for line in raw.splitlines()[-200:]:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
        except Exception as e:
            return f"Failed to read wiretap: {e}"

        if not entries:
            return "Wiretap is empty."

        # Filter to real inbound/outbound traffic (skip internal decision events)
        traffic = [e for e in entries if e.get("dir") in ("inbound", "outbound")]
        internal = [e for e in entries if e.get("dir") == "internal"]

        models = Counter(
            e.get("model", "unknown")
            for e in traffic
            if e.get("role") == "assistant" and e.get("model")
        )
        roles = Counter(e.get("role", "?") for e in traffic)
        errors = [e for e in entries if "error" in e.get("content", "").lower()]
        cache_hits = sum(
            1 for e in internal if "session cache hit" in e.get("content", "")
        )

        q = query.lower()
        want_errors = any(w in q for w in ("error", "fail", "problem", "issue"))
        want_models = any(w in q for w in ("model", "which model", "used"))

        lines = [f"**Wiretap — last {len(entries)} entries**\n"]
        lines.append(f"Traffic events: {len(traffic)}  |  Internal events: {len(internal)}")
        lines.append(f"Messages: {roles.get('user',0)} user, {roles.get('assistant',0)} assistant, {roles.get('system',0)} system")

        if models:
            lines.append("\n**Models used:**")
            for model, count in models.most_common(8):
                lines.append(f"  {count:>3}×  {model}")

        if cache_hits:
            lines.append(f"\nSession cache hits: {cache_hits}")

        if errors or want_errors:
            if errors:
                lines.append(f"\n**Recent errors ({len(errors)}):**")
                for e in errors[-5:]:
                    ts = e.get("ts", "")[:19]
                    lines.append(f"  [{ts}] {e.get('content','')[:120]}")
            else:
                lines.append("\nNo errors found in recent traffic. ✓")

        return "\n".join(lines)
