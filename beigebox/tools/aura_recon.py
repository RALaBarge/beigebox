"""
Aura Recon — discover and replay Salesforce Lightning Aura action descriptors.

Salesforce Lightning blocks the REST API for web sessions, so programmatic
access to record data has to go through the internal Aura framework
(`POST /aura?...`). Every Aura call needs a fresh `aura.token` JWT, a live
`aura.context` (with current `fwuid`), and a valid action descriptor + params.

This tool automates the recon: it talks to BrowserBox (ws://localhost:9009),
installs a one-shot XHR sniffer in the active Salesforce tab via
`inject.aura_actions`, and observes outbound Aura traffic to extract working
`{descriptor, params}` shapes. It can also replay known descriptors via
`inject.aura` and persist them to a local registry.

Config (config.yaml):
    tools:
      aura_recon:
        enabled: true
        ws_url: ws://localhost:9009
        timeout: 15
        default_sniff_seconds: 10

Invocation (single-tool dispatch by method — all input is JSON):
    {"method": "sniff",               "duration_s": 10}
    {"method": "call",                "descriptor": "...", "params": {...}}
    {"method": "discover_for_record", "record_id": "500..."}
    {"method": "save_known",          "name": "...", "descriptor": "...", "params": {...}}
    {"method": "list_known"}

All token/context material is session-sensitive and is NEVER logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from beigebox.tools._bb_client import BBClient

logger = logging.getLogger(__name__)

# Workspace path for the persisted "known descriptors" registry.
_STATE_DIR_DEFAULT = Path(__file__).parent.parent.parent / "workspace" / "state"


def _shape_of(value: Any) -> Any:
    """Return a recursive type-shape of `value`, dropping actual contents."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        if not value:
            return ["empty"]
        return [_shape_of(value[0])]
    if isinstance(value, dict):
        return {k: _shape_of(v) for k, v in value.items()}
    return type(value).__name__


def _clean_body(body: str) -> str:
    """
    Strip Salesforce Aura response prefixes:
      - `while(1);\\n` anti-JSON-hijack prefix
      - `*/...ERROR*/` comment wrapper on error payloads
    """
    if not body:
        return body
    if body.startswith("while(1);"):
        body = body[len("while(1);"):].lstrip("\n")
    # Error payloads: */\n{json}/*ERROR*/  — best-effort extraction
    m = re.match(r"^\s*\*/\s*(.*?)\s*/\*ERROR\*/\s*$", body, re.DOTALL)
    if m:
        body = m.group(1)
    return body


class AuraReconTool:
    """
    MCP-invokable tool: discover and replay Salesforce Aura action descriptors.

    Dispatch is by a JSON `method` field; `run()` returns a JSON string.
    """

    capture_tool_io: bool    = True
    max_context_chars: int   = 6000

    description = (
        "Recon Salesforce Lightning Aura action descriptors by sniffing live "
        "XHR traffic from a logged-in Salesforce browser tab via BrowserBox. "
        'Input is JSON with a "method" field.\n'
        "Methods:\n"
        '  {"method":"sniff","duration_s":10}  → observe /aura traffic, return deduped descriptors\n'
        '  {"method":"call","descriptor":"...","params":{...}}  → invoke a descriptor\n'
        '  {"method":"discover_for_record","record_id":"500..."}  → sniff and filter by record\n'
        '  {"method":"save_known","name":"...","descriptor":"...","params":{...}}\n'
        '  {"method":"list_known"}'
    )

    def __init__(
        self,
        ws_url: str = "ws://localhost:9009",
        timeout: float = 15.0,
        default_sniff_seconds: float = 10.0,
        state_dir: str | Path | None = None,
    ):
        self._ws_url   = ws_url
        self._timeout  = timeout
        self._default_sniff = default_sniff_seconds
        self._state_dir = Path(state_dir) if state_dir else _STATE_DIR_DEFAULT
        self._known_path = self._state_dir / "aura_known.json"

    # ── MCP entry point ────────────────────────────────────────────────

    def run(self, input_str: str) -> str:
        try:
            params = json.loads(input_str.strip()) if input_str and input_str.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": 'input must be JSON with a "method" field'})

        if not isinstance(params, dict):
            return json.dumps({"error": "input must be a JSON object"})

        method = params.get("method", "")
        if not method:
            return json.dumps({"error": 'missing "method" field'})

        try:
            if method == "sniff":
                duration = float(params.get("duration_s", self._default_sniff))
                result = asyncio.run(self.sniff(duration))
            elif method == "call":
                descriptor = params.get("descriptor", "")
                action_params = params.get("params", {}) or {}
                if not descriptor:
                    return json.dumps({"error": 'call requires "descriptor"'})
                result = asyncio.run(self.call(descriptor, action_params))
            elif method == "discover_for_record":
                record_id = params.get("record_id", "")
                if not record_id:
                    return json.dumps({"error": 'discover_for_record requires "record_id"'})
                duration = float(params.get("duration_s", self._default_sniff))
                result = asyncio.run(self.discover_for_record(record_id, duration))
            elif method == "save_known":
                name = params.get("name", "")
                descriptor = params.get("descriptor", "")
                action_params = params.get("params", {}) or {}
                if not name or not descriptor:
                    return json.dumps({"error": 'save_known requires "name" and "descriptor"'})
                result = self.save_known(name, descriptor, action_params)
            elif method == "list_known":
                result = self.list_known()
            else:
                return json.dumps({"error": f"unknown method: {method}"})
        except TimeoutError as e:
            return json.dumps({"error": f"timeout: {e}"})
        except RuntimeError as e:
            # Scrub session-sensitive bits just in case
            return json.dumps({"error": str(e)})
        except Exception as e:
            logger.error("aura_recon: %s failed — %s: %s", method, type(e).__name__, e)
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

        try:
            return json.dumps(result, default=str)
        except Exception:
            return json.dumps({"result": str(result)})

    # ── Methods ────────────────────────────────────────────────────────

    async def sniff(self, duration_s: float = 10.0) -> dict:
        """
        Install the sniffer (if needed), drain any already-buffered actions,
        wait `duration_s`, then drain again and return a deduplicated list of
        {descriptor, params_shape, sample_params, count}.
        """
        client = BBClient(ws_url=self._ws_url, timeout=self._timeout)

        # Prime: install hook + clear any stale entries so we only measure
        # actions fired during the actual window.
        await client.call("inject.aura_actions", {"drain": True})

        await asyncio.sleep(max(0.0, duration_s))

        # Need a fresh client per call (BB closes the socket each call).
        drain_client = BBClient(
            ws_url=self._ws_url,
            timeout=max(self._timeout, duration_s + 5.0),
        )
        raw = await drain_client.call("inject.aura_actions", {"drain": True})

        data    = json.loads(raw) if raw else {}
        actions = data.get("actions", []) if isinstance(data, dict) else []
        has_ctx = bool(data.get("hasContext")) if isinstance(data, dict) else False

        # Dedupe by descriptor, keep first sample + shape + count
        seen: dict[str, dict] = {}
        for a in actions:
            desc = a.get("descriptor") if isinstance(a, dict) else None
            if not desc:
                continue
            params_val = a.get("params", {}) if isinstance(a, dict) else {}
            entry = seen.get(desc)
            if entry is None:
                seen[desc] = {
                    "descriptor":    desc,
                    "params_shape":  _shape_of(params_val),
                    "sample_params": params_val,
                    "count":         1,
                }
            else:
                entry["count"] += 1

        return {
            "duration_s":        duration_s,
            "has_aura_context":  has_ctx,
            "raw_count":         len(actions),
            "descriptors":       list(seen.values()),
            "hint": (
                "interact with the Salesforce tab during the sniff window "
                "to generate outbound /aura requests"
                if not seen else
                f"captured {len(seen)} unique descriptor(s)"
            ),
        }

    async def call(self, descriptor: str, params: dict | None = None) -> dict:
        """
        Thin wrapper over `inject.aura`. Returns the parsed response JSON,
        stripping Salesforce's `while(1);` / `*/...ERROR*/` prefixes.
        """
        client = BBClient(ws_url=self._ws_url, timeout=self._timeout)
        raw = await client.call("inject.aura", {
            "descriptor": descriptor,
            "params":     params or {},
        })
        if not raw:
            return {"status": 0, "error": "inject.aura returned null"}

        wrapper = json.loads(raw)  # {status, body, fwuid?}
        status  = wrapper.get("status", 0)
        body    = _clean_body(wrapper.get("body", "") or "")

        parsed: Any
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            parsed = {"raw": body[:2000]}

        return {
            "status":   status,
            "response": parsed,
        }

    async def discover_for_record(self, record_id: str, duration_s: float = 10.0) -> dict:
        """
        Sniff the Aura pipeline for `duration_s` and filter descriptors whose
        params mention the given record ID (full 18-char or 15-char prefix,
        plus the 3-char sObject key prefix). The caller should navigate to the
        record in the browser during the sniff window.
        """
        result = await self.sniff(duration_s)
        descriptors = result.get("descriptors", [])

        rid_short   = record_id[:15]
        sobj_prefix = record_id[:3]

        def _mentions(obj: Any) -> bool:
            if obj is None:
                return False
            if isinstance(obj, str):
                return record_id in obj or rid_short in obj
            if isinstance(obj, (list, tuple)):
                return any(_mentions(x) for x in obj)
            if isinstance(obj, dict):
                return any(_mentions(v) for v in obj.values())
            return False

        matches = []
        for d in descriptors:
            sample = d.get("sample_params")
            if _mentions(sample):
                matches.append({**d, "match_kind": "record_id"})
                continue
            # Weaker match: sObject key prefix in any string value
            if sobj_prefix and _mentions_prefix(sample, sobj_prefix):
                matches.append({**d, "match_kind": "sobject_prefix"})

        return {
            "record_id":           record_id,
            "sobject_key_prefix":  sobj_prefix,
            "duration_s":          duration_s,
            "has_aura_context":    result.get("has_aura_context", False),
            "total_descriptors":   len(descriptors),
            "matching":            matches,
            "hint": (
                f"navigate to record {record_id} in the Salesforce tab during "
                f"the {duration_s:.0f}s sniff window to generate relevant traffic"
            ) if not matches else f"matched {len(matches)} descriptor(s) to record {record_id}",
        }

    # ── Known-registry persistence ─────────────────────────────────────

    def _load_known(self) -> dict:
        if not self._known_path.exists():
            return {}
        try:
            return json.loads(self._known_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("aura_recon: could not parse %s — %s", self._known_path, e)
            return {}

    def _write_known(self, data: dict) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._known_path.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def save_known(self, name: str, descriptor: str, params: dict | None = None) -> dict:
        known = self._load_known()
        known[name] = {
            "descriptor": descriptor,
            "params":     params or {},
        }
        self._write_known(known)
        return {
            "saved": name,
            "path":  str(self._known_path),
            "count": len(known),
        }

    def list_known(self) -> dict:
        known = self._load_known()
        return {
            "path":  str(self._known_path),
            "count": len(known),
            "items": known,
        }


def _mentions_prefix(obj: Any, prefix: str) -> bool:
    if isinstance(obj, str):
        # Salesforce IDs are 15 or 18 chars and start with the 3-char sObject key
        return bool(re.search(rf"\b{re.escape(prefix)}[A-Za-z0-9]{{12,15}}\b", obj))
    if isinstance(obj, (list, tuple)):
        return any(_mentions_prefix(x, prefix) for x in obj)
    if isinstance(obj, dict):
        return any(_mentions_prefix(v, prefix) for v in obj.values())
    return False
