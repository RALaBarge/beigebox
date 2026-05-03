"""End-to-end tests for the MCP tool-call execution path.

Grok review (honorable mention): test_v3_thin_proxy.py covers MCP
``initialize`` + ``tools/list`` against a live container, but nothing
exercises ``tools/call`` end-to-end — including the unhappy paths
(unknown tool, tool raises, parameter validation failure).

This file uses an in-memory fake ToolRegistry to drive McpServer
through the same JSON-RPC dispatch the real /mcp endpoint uses. The
shape of the responses (``content`` blocks, ``isError`` flag, JSON-RPC
``error`` envelope) is what real MCP clients actually rely on.

Why a fake registry instead of the real one? The real ToolRegistry
imports 30+ tool modules at construction time, several of which need
network/process resources (cdp, browser, etc.) we don't want in unit
tests. The fake gives us full control of tool behaviour while still
exercising the real MCP dispatch code.
"""
from __future__ import annotations

import pytest

from beigebox.mcp_server import McpServer
from beigebox.tools.validation import ParameterValidator


class _FakeRegistry:
    """Minimal stand-in for ToolRegistry used by McpServer.

    Mirrors the surface area McpServer touches: ``tools`` dict + a
    ``run_tool(name, input_text)`` method. Every test creates a fresh
    instance so cross-test state can't leak."""

    def __init__(self) -> None:
        self.tools: dict[str, _FakeTool] = {}
        # Match the real registry's parameter validator hookup so tests
        # exercise the full validation → run pipeline.
        self.validator = ParameterValidator()

    def register(self, name: str, tool: "_FakeTool") -> None:
        self.tools[name] = tool

    def run_tool(self, name: str, input_text: str):
        """Mirror ToolRegistry.run_tool's contract: validate, run,
        catch exceptions, return string-or-None result."""
        tool = self.tools.get(name)
        if tool is None:
            return None
        validation = self.validator.validate_tool_input(name, input_text)
        if not validation.is_valid:
            return f"Error: input validation failed for '{name}': " + "; ".join(validation.errors)
        try:
            return tool.run(validation.cleaned_input or input_text)
        except Exception as e:
            return f"Error: tool '{name}' failed: {e}"


class _FakeTool:
    """Tool that returns whatever ``handler`` produces for the input."""

    def __init__(self, handler, description: str = "fake tool"):
        self._handler = handler
        self.description = description

    def run(self, input_text: str):
        return self._handler(input_text)


def _rpc_call(name: str, input_text: str | None = None, *, arguments: dict | None = None, id_: int = 1) -> dict:
    """Build a JSON-RPC tools/call envelope."""
    args = arguments if arguments is not None else ({"input": input_text} if input_text is not None else {})
    return {"jsonrpc": "2.0", "id": id_, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


# ---------------------------------------------------------------------------
# TestMcpToolsCallSuccess — happy path: tool runs, response shape valid
# ---------------------------------------------------------------------------


class TestMcpToolsCallSuccess:
    @pytest.mark.asyncio
    async def test_registered_tool_runs_and_returns_text_block(self):
        registry = _FakeRegistry()
        registry.register("echo", _FakeTool(lambda x: f"echoed: {x}"))
        server = McpServer(registry, resident_tools=set())  # expose all

        resp = await server.handle(_rpc_call("echo", "hello"))

        assert resp is not None
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1
        assert "result" in resp, f"Expected result, got: {resp}"
        result = resp["result"]
        assert result["isError"] is False
        # Content blocks follow the MCP spec: list of {"type": "text", "text": "..."}
        assert isinstance(result["content"], list)
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "echoed: hello"

    @pytest.mark.asyncio
    async def test_tool_call_with_structured_arguments_falls_back_to_json(self):
        """When ``input`` isn't in arguments, the server JSON-encodes the
        whole arguments dict and passes that to the tool. This is the
        documented fallback for clients that don't use the canonical
        single-input convention."""
        captured: list[str] = []
        registry = _FakeRegistry()
        registry.register("structured", _FakeTool(lambda x: (captured.append(x), "ok")[1]))
        server = McpServer(registry, resident_tools=set())

        resp = await server.handle(
            _rpc_call("structured", arguments={"query": "hello", "limit": 5})
        )

        assert resp["result"]["isError"] is False
        # Tool received a JSON string of the whole arguments dict
        assert captured[0]
        # Both fields preserved through the JSON round-trip
        import json
        parsed = json.loads(captured[0])
        assert parsed["query"] == "hello"
        assert parsed["limit"] == 5


# ---------------------------------------------------------------------------
# TestMcpToolsCallErrors — unhappy paths: unknown / raise / validation fail
# ---------------------------------------------------------------------------


class TestMcpToolsCallErrors:
    """Each failure mode must come back as an MCP-shaped response, NOT
    leak a Python traceback or return a raw 500."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_mcp_error_response_not_jsonrpc_error(self):
        """Unknown-tool is a tool-LEVEL error (isError=True in the result),
        not a JSON-RPC protocol error. The response still has ``result``,
        not the JSON-RPC ``error`` envelope. This distinction matters: a
        well-behaved client prints the ``content`` for the user; a JSON-RPC
        error would crash the client's normal flow."""
        registry = _FakeRegistry()
        server = McpServer(registry, resident_tools=set())

        resp = await server.handle(_rpc_call("nope_does_not_exist", "input"))

        assert "result" in resp
        assert "error" not in resp  # NOT a protocol error
        result = resp["result"]
        assert result["isError"] is True
        # The error message names the tool the client asked for
        assert "nope_does_not_exist" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_tool_that_raises_does_not_leak_traceback(self):
        """A tool's exception must NOT escape ``handle()`` as an unhandled
        Python error. The registry catches it and returns a text-wrapped
        error string; the MCP server packages that as a normal text
        response. The client sees the message but never a traceback.

        Equally important: ``handle()`` does NOT raise, so a misbehaving
        tool can't take down the entire MCP endpoint."""
        registry = _FakeRegistry()

        def _broken(_input):
            raise RuntimeError("simulated tool failure")
        registry.register("broken", _FakeTool(_broken))
        server = McpServer(registry, resident_tools=set())

        # If the exception leaked, this would raise. The point of the test
        # is the lack of an exception more than the response shape.
        resp = await server.handle(_rpc_call("broken", "x"))

        assert "result" in resp, f"Got JSON-RPC error instead of result: {resp}"
        text = resp["result"]["content"][0]["text"]
        # Error message reaches the client — but it's a clean string,
        # not a traceback (no "File "/tmp/...py", line ...")
        assert "simulated tool failure" in text or "failed" in text
        assert "Traceback" not in text
        assert ".py\"" not in text  # path-like substring from a real traceback

    @pytest.mark.asyncio
    async def test_missing_tool_name_returns_jsonrpc_invalid_params(self):
        """``tools/call`` without a ``name`` is a protocol-level error —
        the JSON-RPC ``-32602 Invalid params`` code is correct here.
        Any other response would suggest the call succeeded."""
        registry = _FakeRegistry()
        server = McpServer(registry, resident_tools=set())

        resp = await server.handle({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"arguments": {"input": "x"}},  # name missing
        })

        assert "error" in resp
        assert resp["error"]["code"] == -32602  # Invalid params
        assert "name" in resp["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_oversized_input_rejected_with_jsonrpc_invalid_params(self):
        """The 1 MB input cap is enforced at the MCP boundary, before the
        tool sees the input. Clients can't smuggle a 100 MB payload past
        the registry's parameter validator by going through MCP."""
        registry = _FakeRegistry()
        registry.register("any", _FakeTool(lambda x: "ok"))
        server = McpServer(registry, resident_tools=set())

        big_input = "A" * (1_000_001)  # one byte over the limit
        resp = await server.handle(_rpc_call("any", big_input))

        assert "error" in resp
        assert resp["error"]["code"] == -32602
        assert "too large" in resp["error"]["message"].lower()


# ---------------------------------------------------------------------------
# TestMcpInitializeAndList — handshake + listing surface (not failure paths)
# ---------------------------------------------------------------------------


class TestMcpInitializeAndList:
    """Cross-check: every MCP method has a different shape of response.
    Catches regressions where a refactor accidentally crosses the wires."""

    @pytest.mark.asyncio
    async def test_initialize_returns_server_info(self):
        registry = _FakeRegistry()
        server = McpServer(registry)

        resp = await server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26",
                        "clientInfo": {"name": "claude-desktop", "version": "1.0"}},
        })

        assert resp["jsonrpc"] == "2.0"
        info = resp["result"]["serverInfo"]
        assert info["name"] == "beigebox"
        assert "version" in info
        # Tools capability is always advertised
        assert "tools" in resp["result"]["capabilities"]

    @pytest.mark.asyncio
    async def test_tools_list_includes_registered_resident_tool(self):
        registry = _FakeRegistry()
        registry.register("calculator", _FakeTool(lambda x: "42", description="Math tool"))
        server = McpServer(registry, resident_tools={"calculator"})

        resp = await server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })

        names = [t["name"] for t in resp["result"]["tools"]]
        assert "calculator" in names
        # Schema shape is what the MCP spec requires
        calc = next(t for t in resp["result"]["tools"] if t["name"] == "calculator")
        assert calc["description"] == "Math tool"
        assert calc["inputSchema"]["type"] == "object"
        assert "input" in calc["inputSchema"]["properties"]

    @pytest.mark.asyncio
    async def test_unknown_method_returns_method_not_found(self):
        """JSON-RPC ``-32601`` for unknown methods — clients distinguish
        this from "method exists but failed" (which would be -32603)."""
        registry = _FakeRegistry()
        server = McpServer(registry, resident_tools=set())

        resp = await server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "made/up/method", "params": {},
        })

        assert "error" in resp
        assert resp["error"]["code"] == -32601
        assert "made/up/method" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_notification_returns_none_no_response_to_client(self):
        """Per MCP / JSON-RPC: a notification has no ``id`` field and
        MUST NOT receive a response. Returning anything would break
        clients that aren't waiting for a reply."""
        registry = _FakeRegistry()
        server = McpServer(registry, resident_tools=set())

        resp = await server.handle({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        })
        # No id, so no reply
        assert resp is None

    @pytest.mark.asyncio
    async def test_invalid_jsonrpc_version_rejected(self):
        """An ``"jsonrpc": "1.0"`` request must be rejected with
        ``-32600 Invalid Request`` — otherwise we'd silently accept malformed
        protocol traffic from buggy clients."""
        registry = _FakeRegistry()
        server = McpServer(registry, resident_tools=set())

        resp = await server.handle({
            "jsonrpc": "1.0", "id": 1, "method": "initialize", "params": {},
        })

        assert "error" in resp
        assert resp["error"]["code"] == -32600
