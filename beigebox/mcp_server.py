"""
MCP (Model Context Protocol) server for BeigeBox.

Implements the Streamable HTTP transport (protocol version 2025-03-26).
Exposes BeigeBox's tool registry as MCP tools with progressive disclosure:

  - Resident tools: always visible in tools/list (8-15 common tools)
  - Extended tools: hidden behind discover_tools meta-tool
  - discover_tools: keyword search over capability index, returns 5 candidates

This prevents context flooding when the registry grows large and improves
tool selection quality (fewer choices = better decisions).

Endpoint: POST /mcp

Supported JSON-RPC 2.0 methods:
  initialize     — handshake, returns server capabilities
  tools/list     — list resident tools + discover_tools meta-tool
  tools/call     — invoke any tool by name (resident or extended)
  resources/list — list operator skills
  resources/read — read a skill's markdown content

Notifications (no id field) are accepted and silently acknowledged:
  notifications/initialized

Auth: governed by the same ApiKeyMiddleware as all other BeigeBox
endpoints. Add /mcp to a key's allowed_endpoints to grant MCP access.

Usage (Claude Desktop claude_desktop_config.json):
  {
    "mcpServers": {
      "beigebox": {
        "url": "http://localhost:1337/mcp",
        "headers": {"Authorization": "Bearer YOUR_KEY"}
      }
    }
  }
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"
from beigebox import __version__ as _BB_VERSION
_SERVER_INFO = {"name": "beigebox", "version": _BB_VERSION}

# ---------------------------------------------------------------------------
# Resident tool set — always exposed in tools/list.
# These are the most-used tools that benefit from always being in context.
# Extended tools (anything not in this set) are discoverable via discover_tools.
# Override via config: mcp.resident_tools list.
# ---------------------------------------------------------------------------

_DEFAULT_RESIDENT_TOOLS = {
    "web_search",
    "web_scraper",
    "calculator",
    "datetime",
    "memory",
    "document_search",
    "cdp",
}

_OPERATOR_RUN_SCHEMA = {
    "name": "operator/run",
    "description": (
        "Run BeigeBox Operator — a JSON-based ReAct agent with access to tools "
        "(web_search, calculator, memory, document_search, etc.). "
        "Submit a question or task; the operator reasons step by step and returns a final answer."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "The question or task for the operator to complete.",
            }
        },
        "required": ["input"],
    },
}

_DISCOVER_TOOLS_SCHEMA = {
    "name": "discover_tools",
    "description": (
        "Search for additional tools beyond the default resident set. "
        "Describe the task you are trying to accomplish; returns up to 5 relevant tools "
        "with summaries. After discovery, call the returned tool by name directly. "
        "Use this when no visible tool clearly fits your task."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "Describe the task or capability you are looking for. "
                               "Example: 'I need to take a screenshot of a web page' or "
                               "'I need to read a PDF file'.",
            }
        },
        "required": ["input"],
    },
}


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _ok(id_: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Tool descriptor builder
# ---------------------------------------------------------------------------

def _tool_schema(name: str, tool) -> dict:
    """
    Build an MCP tool descriptor from a BeigeBox tool.

    All BeigeBox tools accept a single string input via tool.run(input_str).
    The input schema reflects this with a single "input" property.
    Tools that document structured JSON input use the same property — the
    description guides the model on what to pass.
    """
    description = getattr(tool, "description", name)
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Tool input. See description for expected format.",
                }
            },
            "required": ["input"],
        },
    }


# ---------------------------------------------------------------------------
# Capability index entry — compact catalog record for a single tool.
# Used by discover_tools to search without exposing full schemas.
# ---------------------------------------------------------------------------

class _CapabilityEntry:
    """Compact representation of a tool for the discovery index."""

    __slots__ = ("name", "summary", "tags", "risk")

    def __init__(self, name: str, tool) -> None:
        self.name = name
        # One-sentence summary — first sentence of the description, or the full
        # description if it is already short.
        desc: str = getattr(tool, "description", name) or name
        first_sentence = desc.split(".")[0].strip()
        self.summary = (first_sentence[:120] + "…") if len(first_sentence) > 120 else first_sentence
        # Tags derived from the tool name (word fragments) — cheap but effective
        # for keyword matching.  Tools can expose CAPABILITY_TAGS to override.
        self.tags: list[str] = list(getattr(tool, "capability_tags", _name_to_tags(name)))
        # Risk level — read or write/mutating.  Tools can expose CAPABILITY_RISK.
        self.risk: str = getattr(tool, "capability_risk", "read")

    def score(self, query_tokens: list[str]) -> int:
        """Return keyword match score for a set of lowercase query tokens."""
        text = (self.name + " " + self.summary + " " + " ".join(self.tags)).lower()
        return sum(1 for t in query_tokens if t in text)


def _name_to_tags(name: str) -> list[str]:
    """Split 'web_scraper' → ['web', 'scraper']."""
    return [part for part in name.replace("-", "_").split("_") if part]


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

class McpServer:
    """
    Stateless MCP request handler with progressive tool disclosure.

    Tools are split into two tiers:
      - Resident:  always in tools/list — the most-used tools.
      - Extended:  hidden; discoverable via the discover_tools meta-tool.

    This keeps the model's tool context small (better selection quality) while
    still allowing any registered tool to be called by name.

    operator_factory: optional async callable (question: str) -> str
        When provided, adds operator/run as an MCP tool that external clients
        (Claude Desktop, AMF mesh workers, etc.) can invoke.

    skills: optional list of skill dicts (name, description, path, dir, metadata)
        When provided, exposed via resources/list and resources/read so MCP
        clients can browse skills without injecting them into every prompt.

    resident_tools: optional set of tool names to always expose in tools/list.
        Defaults to _DEFAULT_RESIDENT_TOOLS. Pass an empty set to expose all
        tools (disables progressive disclosure).
    """

    def __init__(
        self,
        tool_registry,
        operator_factory: Callable[[str], Awaitable[str]] | None = None,
        skills: list | None = None,
        resident_tools: set[str] | None = None,
        server_label: str = "mcp",
    ):
        self._registry = tool_registry
        self._operator_factory = operator_factory
        self._skills = skills or []
        # resident_tools=None → use default set.  resident_tools=set() → expose all.
        self._resident_tools: set[str] | None = resident_tools
        # server_label distinguishes endpoints (e.g. "mcp" vs "pen-mcp") in
        # wire events emitted from _tools_call. Defaults to the canonical /mcp.
        self._server_label = server_label

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_resident_set(self) -> set[str]:
        """Return the effective resident tool set.

        - resident_tools=None      → use the default resident set
        - resident_tools=set()     → expose ALL registered tools (disables
                                     progressive disclosure)
        - resident_tools={'a','b'} → expose exactly those tools
        """
        if self._resident_tools is None:
            return _DEFAULT_RESIDENT_TOOLS
        if not self._resident_tools:
            # Empty set: caller wants every tool resident.
            return set(self._registry.tools.keys())
        return self._resident_tools

    def _build_capability_index(self) -> list[_CapabilityEntry]:
        """Build compact index entries for all NON-resident registered tools."""
        resident = self._get_resident_set()
        entries = []
        for name, tool in self._registry.tools.items():
            if name not in resident:
                entries.append(_CapabilityEntry(name, tool))
        # operator/run is always resident when enabled; skip from extended index
        return entries

    def _search_capabilities(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Keyword-rank the capability index against a free-text query.
        Returns up to top_k candidates as summary dicts.
        """
        tokens = [t.lower() for t in query.replace(",", " ").split() if len(t) > 2]
        if not tokens:
            # No useful tokens — return first top_k entries
            index = self._build_capability_index()
            candidates = index[:top_k]
        else:
            index = self._build_capability_index()
            scored = [(entry, entry.score(tokens)) for entry in index]
            scored.sort(key=lambda x: x[1], reverse=True)
            candidates = [entry for entry, score in scored if score > 0][:top_k]
            if not candidates:
                candidates = index[:top_k]  # fallback: return first N when no match

        return [
            {
                "tool": e.name,
                "summary": e.summary,
                "risk": e.risk,
                "tags": e.tags,
            }
            for e in candidates
        ]

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    async def handle(self, body: dict) -> dict | None:
        """
        Dispatch a JSON-RPC request.

        Returns a response dict, or None for notifications
        (which must not receive a response per the MCP spec).
        """
        if body.get("jsonrpc") != "2.0":
            return _err(None, -32600, "jsonrpc must be '2.0'")

        method: str = body.get("method", "")
        id_: Any = body.get("id")          # None → notification
        params: dict = body.get("params") or {}

        # JSON-RPC notifications have no "id" field. The MCP spec forbids
        # sending a response to a notification, so we return None and the
        # FastAPI endpoint converts that to HTTP 202 Accepted.
        if id_ is None:
            if method not in ("notifications/initialized", "initialized"):
                logger.debug("MCP: unrecognised notification '%s' — ignored", method)
            return None

        try:
            if method == "initialize":
                return _ok(id_, self._initialize(params))
            if method == "tools/list":
                return _ok(id_, self._tools_list())
            if method == "tools/call":
                return _ok(id_, await self._tools_call(params))
            if method == "resources/list":
                return _ok(id_, self._resources_list())
            if method == "resources/read":
                return _ok(id_, self._resources_read(params))
            return _err(id_, -32601, f"Method not found: {method}")
        except ValueError as e:
            return _err(id_, -32602, str(e))
        except Exception as e:
            logger.error("MCP internal error (method=%s): %s", method, e)
            return _err(id_, -32603, f"Internal error: {e}")

    # ------------------------------------------------------------------
    # Method handlers
    # ------------------------------------------------------------------

    def _initialize(self, params: dict) -> dict:
        client_version = params.get("protocolVersion", "unknown")
        client_info = params.get("clientInfo", {})
        logger.info(
            "MCP initialize: client=%s/%s protocolVersion=%s",
            client_info.get("name", "?"), client_info.get("version", "?"), client_version,
        )
        caps: dict = {"tools": {}}
        if self._skills:
            caps["resources"] = {}

        # Count resident vs extended for the instructions string
        resident = self._get_resident_set()
        n_resident = sum(1 for name in self._registry.tools if name in resident)
        n_extended = len(self._registry.tools) - n_resident
        discovery_note = (
            f" {n_extended} additional tools are available via discover_tools."
            if n_extended > 0 else ""
        )

        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": _SERVER_INFO,
            "capabilities": caps,
            "instructions": (
                f"BeigeBox MCP server. {n_resident} resident tools are always available. "
                "Use tools/call to invoke them. All tools accept a single 'input' string argument. "
                "See each tool's description for the expected format."
                + discovery_note
                + (" Use resources/list to browse operator skills." if self._skills else "")
            ),
        }

    def _tools_list(self) -> dict:
        """
        Return only resident tools + discover_tools meta-tool (if extended tools exist).

        The full registry is still callable by name — progressive disclosure only
        affects what is advertised in tools/list, not what can be invoked.
        """
        resident = self._get_resident_set()
        tools = []

        for name, tool in self._registry.tools.items():
            if name in resident:
                tools.append(_tool_schema(name, tool))

        if self._operator_factory is not None:
            tools.append(_OPERATOR_RUN_SCHEMA)

        # Add discover_tools only when there are hidden tools to find
        n_extended = len(self._registry.tools) - len(tools)
        if n_extended > 0:
            tools.append(_DISCOVER_TOOLS_SCHEMA)

        logger.debug(
            "MCP tools/list: %d resident + discover_tools (%d extended hidden)",
            len(tools), n_extended,
        )
        return {"tools": tools}

    def _resources_list(self) -> dict:
        resources = [
            {
                "uri": f"skill://{s['name']}",
                "name": s["name"],
                "description": s.get("description", ""),
                "mimeType": "text/markdown",
            }
            for s in self._skills
        ]
        logger.debug("MCP resources/list: %d skills", len(resources))
        return {"resources": resources}

    def _resources_read(self, params: dict) -> dict:
        uri: str = params.get("uri", "").strip()
        if not uri.startswith("skill://"):
            raise ValueError(f"Unknown resource URI scheme: {uri!r}. Expected skill://<name>")
        name = uri[len("skill://"):]
        skill = next((s for s in self._skills if s["name"] == name), None)
        if skill is None:
            known = ", ".join(s["name"] for s in self._skills) or "(none)"
            raise ValueError(f"Skill '{name}' not found. Available: {known}")
        try:
            from pathlib import Path as _Path
            text = _Path(skill["path"]).read_text(encoding="utf-8")
        except Exception as e:
            raise ValueError(f"Failed to read skill '{name}': {e}") from e
        return {
            "contents": [
                {"uri": uri, "mimeType": "text/markdown", "text": text}
            ]
        }

    async def _tools_call(self, params: dict) -> dict:
        import time as _time
        from beigebox.logging import log_tool_call as _log_tool_call

        # Start the timer FIRST so even early validation errors get a wire
        # event (catches the input-size raise + any other ValueError below).
        _t0 = _time.monotonic()
        name: str = (params.get("name") or "").strip()
        # Mutable holder lets the inner code "set" the outcome; the finally
        # block fires the event exactly once. If no inner code set a status,
        # the finally records it as an unhandled error — guards against a
        # future return path forgetting to call _emit().
        _outcome: dict = {"status": None, "error": None, "input_length": 0}

        def _emit(status: str, error: str | None = None) -> None:
            _outcome["status"] = status
            _outcome["error"] = error

        try:
            arguments: dict = params.get("arguments") or {}

            if not name:
                _emit("error", error="missing_name")
                raise ValueError("tools/call requires 'name'")

            # Prefer the idiomatic "input" key that our tool schema defines.
            # Fall back to JSON-encoding the whole arguments dict so callers
            # that pass structured arguments (e.g. {"query": "..."}) still work.
            if "input" in arguments:
                input_text = str(arguments["input"])
            else:
                input_text = json.dumps(arguments)
            _outcome["input_length"] = len(input_text)

            _MCP_INPUT_LIMIT = 1_000_000  # 1 MB
            if len(input_text) > _MCP_INPUT_LIMIT:
                _emit("error", error="input_too_large")
                raise ValueError(f"Input too large ({len(input_text)} chars, limit {_MCP_INPUT_LIMIT})")

            logger.info("MCP tools/call: %s (input=%r)", name, input_text[:120])
            return await self._tools_call_dispatch(name, input_text, _emit)
        finally:
            # If no inner branch set a status, treat it as an unhandled
            # exception — keeps the "exactly one event per call" guarantee.
            if _outcome["status"] is None:
                _outcome["status"] = "error"
                _outcome["error"] = _outcome["error"] or "unhandled_exception"
            _log_tool_call(
                tool_name=name or "(no_name)",
                status=_outcome["status"],
                latency_ms=(_time.monotonic() - _t0) * 1000,
                error=_outcome["error"],
                source=self._server_label,
                extra_meta={"server": self._server_label,
                            "input_length": _outcome["input_length"]},
            )

    async def _tools_call_dispatch(self, name: str, input_text: str, _emit) -> dict:
        """Inner dispatch — split out so _tools_call can wrap it in try/finally."""

        # discover_tools — search the capability index and return summaries
        if name == "discover_tools":
            candidates = self._search_capabilities(input_text)
            if not candidates:
                text = (
                    "No matching tools found for that query. "
                    "Available tool names: " + ", ".join(self._registry.tools.keys())
                )
            else:
                lines = [
                    f"Found {len(candidates)} tool(s) matching your query. "
                    "Call any of them directly by name:\n"
                ]
                for c in candidates:
                    risk_marker = " [write]" if c["risk"] == "write" else ""
                    lines.append(f"- **{c['tool']}**{risk_marker}: {c['summary']}")
                text = "\n".join(lines)
            _emit("ok")
            return {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            }

        # operator/run — dispatches to the BeigeBox Operator agent
        if name == "operator/run":
            if self._operator_factory is None:
                _emit("error", error="operator_disabled")
                return {
                    "content": [{"type": "text", "text": "operator/run is not available (operator disabled or not configured)."}],
                    "isError": True,
                }
            try:
                answer = await self._operator_factory(input_text)
                _emit("ok")
                return {
                    "content": [{"type": "text", "text": answer or "(no result)"}],
                    "isError": False,
                }
            except Exception as e:
                logger.error("MCP operator/run error: %s", e)
                _emit("error", error=str(e)[:200])
                return {
                    "content": [{"type": "text", "text": f"Operator error: {e}"}],
                    "isError": True,
                }

        # Any registered tool — resident OR extended (both callable by name)
        if name not in self._registry.tools:
            known = ", ".join(self._registry.tools.keys()) or "(none)"
            if self._operator_factory is not None:
                known += ", operator/run"
            known += ", discover_tools"
            _emit("error", error="tool_not_found")
            return {
                "content": [{"type": "text", "text": f"Tool '{name}' not found. Available: {known}"}],
                "isError": True,
            }

        result = self._registry.run_tool(name, input_text)

        if result is None:
            _emit("error", error="no_result")
            return {
                "content": [{"type": "text", "text": f"Tool '{name}' returned no result."}],
                "isError": True,
            }

        # Structured MCP content (e.g. images from cdp.screenshot) — pass through
        # the native content blocks so vision-capable clients receive them as-is.
        from beigebox.tools._media import MCP_CONTENT_KEY
        if isinstance(result, dict) and MCP_CONTENT_KEY in result:
            _emit("ok")
            return {
                "content": result[MCP_CONTENT_KEY],
                "isError": False,
            }

        _emit("ok")
        return {
            "content": [{"type": "text", "text": result}],
            "isError": False,
        }
