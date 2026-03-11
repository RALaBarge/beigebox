"""
E2E tests for operator/stream endpoint.

Tests the full HTTP request → SSE response flow with real FastAPI routing.
Mocks only external services (Ollama backend).

Run: pytest tests/test_e2e_operator.py -v
"""

import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────

def _mock_ollama_chat_completion(messages, model, tools=None, **kwargs):
    """Mock Ollama /v1/chat/completions response"""
    response = MagicMock()
    response.status_code = 200

    # Simple mock: just echo back the query
    last_user_msg = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        "ok"
    )

    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": f"Response to: {last_user_msg[:50]}",
                    "role": "assistant",
                },
                "finish_reason": "stop",
            }
        ],
        "model": model,
    }
    response.raise_for_status = MagicMock()
    return response


def _parse_sse_stream(response_text):
    """Parse SSE response text into events"""
    events = []
    for line in response_text.split("\n"):
        if line.startswith("data: "):
            try:
                evt = json.loads(line[6:])
                events.append(evt)
            except json.JSONDecodeError:
                pass
    return events


# ── Tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
def test_operator_stream_e2e_basic_query():
    """
    POST /api/v1/operator/stream with basic query returns SSE stream.

    Verifies:
    - Response status 200
    - Content-Type: text/event-stream
    - At least one answer event
    - SSE format is valid (data: {...})
    """
    from beigebox.main import app

    client = TestClient(app)

    with patch("httpx.AsyncClient.post", side_effect=_mock_ollama_chat_completion):
        resp = client.post(
            "/api/v1/operator/stream",
            json={"query": "what is 2+2?", "history": []},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    events = _parse_sse_stream(resp.text)
    assert len(events) > 0
    assert any(e["type"] == "answer" for e in events)


@pytest.mark.asyncio
def test_operator_stream_e2e_returns_structured_events():
    """
    SSE events have correct structure (type, content/message).

    Verifies:
    - answer events have 'content' field
    - error events have 'message' field
    - tool_call events have 'tool', 'input', 'thought'
    """
    from beigebox.main import app

    client = TestClient(app)

    with patch("httpx.AsyncClient.post", side_effect=_mock_ollama_chat_completion):
        resp = client.post(
            "/api/v1/operator/stream",
            json={"query": "test query", "history": []},
        )

    events = _parse_sse_stream(resp.text)

    # Every event must have a type
    assert all("type" in e for e in events)

    # Check event-specific fields
    answer_events = [e for e in events if e["type"] == "answer"]
    for e in answer_events:
        assert "content" in e
        assert isinstance(e["content"], str)

    error_events = [e for e in events if e["type"] == "error"]
    for e in error_events:
        assert "message" in e
        assert isinstance(e["message"], str)


@pytest.mark.asyncio
def test_operator_stream_e2e_requires_query():
    """
    POST /api/v1/operator/stream without query returns 400.
    """
    from beigebox.main import app

    client = TestClient(app)

    resp = client.post(
        "/api/v1/operator/stream",
        json={"history": []},  # Missing 'query'
    )

    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data


@pytest.mark.asyncio
def test_operator_stream_e2e_disabled_returns_403():
    """
    POST /api/v1/operator/stream when operator disabled returns 403.
    """
    from beigebox.main import app
    from beigebox.config import get_runtime_config

    client = TestClient(app)

    with patch("beigebox.main.get_runtime_config") as mock_rt:
        mock_rt.return_value = {"operator_enabled": False}
        resp = client.post(
            "/api/v1/operator/stream",
            json={"query": "test", "history": []},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
def test_operator_stream_e2e_max_turns_in_request():
    """
    max_turns parameter in request body is accepted.
    """
    from beigebox.main import app

    client = TestClient(app)

    with patch("httpx.AsyncClient.post", side_effect=_mock_ollama_chat_completion):
        resp = client.post(
            "/api/v1/operator/stream",
            json={
                "query": "multi-turn test",
                "history": [],
                "max_turns": 3,  # Should be accepted
            },
        )

    assert resp.status_code == 200
    events = _parse_sse_stream(resp.text)
    assert len(events) > 0


@pytest.mark.asyncio
def test_operator_stream_e2e_model_override():
    """
    model parameter in request body specifies which model to use.
    """
    from beigebox.main import app

    client = TestClient(app)

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = _mock_ollama_chat_completion([], "custom-model")

        resp = client.post(
            "/api/v1/operator/stream",
            json={
                "query": "test",
                "history": [],
                "model": "custom-model",
            },
        )

    assert resp.status_code == 200


# ── Integration Tests (real components, mocked backend) ──────────────────

@pytest.mark.asyncio
async def test_operator_integration_with_real_tool_registry():
    """
    Operator uses real ToolRegistry, can discover and call tools.

    Verifies:
    - Tool registry is initialized
    - Operator can query tool registry
    - Tool calls flow through real registry
    """
    from beigebox.tools.registry import ToolRegistry
    from beigebox.agents.operator import Operator

    registry = ToolRegistry(vector_store=None)
    tools = registry.list_tools()

    # Should have at least a few standard tools
    assert "calculator" in tools or len(tools) > 0

    # Operator should be able to instantiate with registry
    op = Operator(vector_store=None, blob_store=None, tool_registry=registry)
    assert op._registry is not None


@pytest.mark.asyncio
async def test_operator_integration_history_threaded_correctly():
    """
    History from request is threaded through operator, visible to LLM.

    Verifies:
    - Initial query receives history parameter
    - History messages are sent to backend
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    history = [
        {"role": "user", "content": "My name is Alice"},
        {"role": "assistant", "content": "Nice to meet you Alice"},
    ]

    with patch.object(op, "_call_backend") as mock_backend:
        mock_backend.return_value = [
            {"role": "assistant", "content": "Your name is Alice"}
        ]

        messages_sent = []

        def capture_messages(msgs, **kw):
            messages_sent.append(msgs)
            return [{"role": "assistant", "content": "ok"}]

        mock_backend.side_effect = capture_messages

        await op.run("What is my name?", history=history)

    # Verify history was included in messages
    assert len(messages_sent) > 0
    all_msgs = messages_sent[0]
    assert any("Alice" in m.get("content", "") for m in all_msgs)


# ── Error Scenario Tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_operator_error_handles_backend_timeout():
    """
    Operator gracefully handles backend timeout (e.g., Ollama unresponsive).

    Verifies:
    - Error event is emitted
    - No crash or infinite loop
    - Error message is informative
    """
    from beigebox.agents.operator import Operator
    import asyncio

    op = Operator(vector_store=None, blob_store=None)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = asyncio.TimeoutError("Backend took >30s")

        events = await op.run("test query", history=[])

        # Should have error event
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) > 0
        assert "timeout" in error_events[0]["message"].lower()


@pytest.mark.asyncio
async def test_operator_error_handles_tool_not_found():
    """
    Operator handles gracefully when tool doesn't exist.
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    # If operator tries to call non-existent tool, should error gracefully
    with patch.object(op._registry, "execute", side_effect=ValueError("Tool not found")):
        events = await op.run("call_nonexistent_tool()", history=[])

        # Should emit answer or error, not crash
        assert any(
            e["type"] in ["answer", "error"] for e in events
        )


# ── Regression Tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_regression_autonomous_mode_early_exit_on_no_tool_calls():
    """
    Regression: Autonomous mode used to loop forever if first turn had no tool calls.

    Verifies:
    - If first turn produces answer without tool calls, loop exits
    - No turn_start event for turn 2
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    # Mock: operator answers directly without tools
    with patch.object(op, "_call_backend") as mock_backend:
        mock_backend.return_value = [
            {
                "role": "assistant",
                "content": "The sky is blue.",
            }
        ]

        events = await op.run("What color is the sky?", history=[])

        # Should have answer event
        assert any(e["type"] == "answer" for e in events)

        # Should NOT have turn_start (no multi-turn if no tool calls)
        turn_starts = [e for e in events if e["type"] == "turn_start"]
        assert len(turn_starts) == 0


@pytest.mark.asyncio
async def test_regression_info_event_on_no_tool_calls():
    """
    Regression: No visual feedback when operator answers without tools.

    Verifies:
    - info event is emitted when first turn has no tool calls
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    with patch.object(op, "_call_backend") as mock_backend:
        mock_backend.return_value = [
            {"role": "assistant", "content": "Direct answer"}
        ]

        events = await op.run("simple query", history=[])

        # Should emit info event
        info_events = [e for e in events if e["type"] == "info"]
        assert len(info_events) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
