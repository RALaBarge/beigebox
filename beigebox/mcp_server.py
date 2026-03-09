"""
MCP (Model Context Protocol) server for BeigeBox.

Implements the Streamable HTTP transport (protocol version 2025-03-26).
Exposes BeigeBox's tool registry as MCP tools.

Endpoint: POST /mcp

Supported JSON-RPC 2.0 methods:
  initialize     — handshake, returns server capabilities
  tools/list     — list available tools with input schemas
  tools/call     — invoke a tool by name

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
from typing import Any

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"
_SERVER_INFO = {"name": "beigebox", "version": "1.0.1"}


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _ok(id_: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Tool descriptor
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
# MCP server
# ---------------------------------------------------------------------------

class McpServer:
    """
    Stateless MCP request handler. Thread-safe — shares the tool registry
    with the rest of BeigeBox but never mutates it.
    """

    def __init__(self, tool_registry):
        self._registry = tool_registry

    def handle(self, body: dict) -> dict | None:
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
                return _ok(id_, self._tools_call(params))
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
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": _SERVER_INFO,
            "capabilities": {"tools": {}},
            "instructions": (
                "BeigeBox MCP server. Use tools/list to discover available tools "
                "and tools/call to invoke them. All tools accept a single 'input' "
                "string argument. See each tool's description for the expected format."
            ),
        }

    def _tools_list(self) -> dict:
        tools = [_tool_schema(name, tool) for name, tool in self._registry.tools.items()]
        logger.debug("MCP tools/list: %d tools", len(tools))
        return {"tools": tools}

    def _tools_call(self, params: dict) -> dict:
        name: str = params.get("name", "").strip()
        arguments: dict = params.get("arguments") or {}

        if not name:
            raise ValueError("tools/call requires 'name'")

        # Prefer the idiomatic "input" key that our tool schema defines.
        # Fall back to JSON-encoding the whole arguments dict so callers that
        # pass structured arguments (e.g. {"query": "..."}) still work.
        if "input" in arguments:
            input_text = str(arguments["input"])
        else:
            input_text = json.dumps(arguments)

        logger.info("MCP tools/call: %s (input=%r)", name, input_text[:120])

        if name not in self._registry.tools:
            known = ", ".join(self._registry.tools.keys()) or "(none)"
            return {
                "content": [{"type": "text", "text": f"Tool '{name}' not found. Available: {known}"}],
                "isError": True,
            }

        result = self._registry.run_tool(name, input_text)

        if result is None:
            return {
                "content": [{"type": "text", "text": f"Tool '{name}' returned no result."}],
                "isError": True,
            }

        return {
            "content": [{"type": "text", "text": result}],
            "isError": False,
        }
