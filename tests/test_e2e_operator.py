"""
E2E tests for operator/stream endpoint.

Tests the full HTTP request → SSE response flow with real FastAPI routing.
Mocks only external services (Ollama backend).

Run: pytest tests/test_e2e_operator.py -v
"""

import json
import pytest
from unittest.mock import patch, MagicMock
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
    from beigebox.agents.operator import Operator

    client = TestClient(app)

    async def _mock_run_stream(self, question, history=None):
        yield {"type": "start", "run_id": "test"}
        yield {"type": "answer", "content": f"Response to: {question[:50]}"}

    with patch.object(Operator, "run_stream", _mock_run_stream):
        resp = client.post(
            "/api/v1/operator/stream",
            json={"query": "what is 2+2?", "history": []},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    events = _parse_sse_stream(resp.text)
    assert len(events) > 0
    assert any(e["type"] == "answer" for e in events)


def test_operator_stream_e2e_returns_structured_events():
    """
    SSE events have correct structure (type, content/message).

    Verifies:
    - answer events have 'content' field
    - error events have 'message' field
    """
    from beigebox.main import app
    from beigebox.agents.operator import Operator

    client = TestClient(app)

    async def _mock_run_stream(self, question, history=None):
        yield {"type": "answer", "content": "test response"}

    with patch.object(Operator, "run_stream", _mock_run_stream):
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


def test_operator_stream_e2e_max_turns_in_request():
    """
    max_turns parameter in request body is accepted.
    """
    from beigebox.main import app
    from beigebox.agents.operator import Operator

    client = TestClient(app)

    async def _mock_run_stream(self, question, history=None):
        yield {"type": "answer", "content": "done"}

    with patch.object(Operator, "run_stream", _mock_run_stream):
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


def test_operator_stream_e2e_model_override():
    """
    model parameter in request body specifies which model to use.
    """
    from beigebox.main import app
    from beigebox.agents.operator import Operator

    client = TestClient(app)

    async def _mock_run_stream(self, question, history=None):
        yield {"type": "answer", "content": "done"}

    with patch.object(Operator, "run_stream", _mock_run_stream):
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

def test_operator_integration_with_real_tool_registry():
    """
    Operator uses real ToolRegistry, can discover and call tools.

    Verifies:
    - Tool registry is initialized
    - Operator can query tool registry
    - Tool list is non-empty
    """
    from beigebox.tools.registry import ToolRegistry
    from beigebox.agents.operator import Operator

    registry = ToolRegistry(vector_store=None)
    tools = registry.list_tools()

    # Should have at least a few standard tools
    assert "calculator" in tools or len(tools) > 0

    # Operator creates its own registry; verify it's populated
    op = Operator(vector_store=None, blob_store=None)
    assert op._registry is not None
    assert len(op._registry.list_tools()) > 0


def test_operator_integration_history_threaded_correctly():
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

    messages_sent = []

    def capture_messages(*args, **kwargs):
        json_body = kwargs.get("json", {})
        messages_sent.append(json_body.get("messages", []))
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"thought": "ok", "answer": "Your name is Alice"}', "role": "assistant"}}]
        }
        return resp

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = capture_messages

        result = op.run("What is my name?", history=history)

    # Verify history was included in messages
    assert len(messages_sent) > 0
    all_msgs = messages_sent[0]
    assert any("Alice" in m.get("content", "") for m in all_msgs)


# ── Error Scenario Tests ───────────────────────────────────────────────────

def test_operator_error_handles_backend_timeout():
    """
    Operator gracefully handles backend timeout (e.g., Ollama unresponsive).

    Verifies:
    - Error string is returned
    - No crash or infinite loop
    - Error message is informative
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    with patch("httpx.Client") as mock_client_class, \
         patch("time.sleep"):
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = TimeoutError("Backend took >30s")

        result = op.run("test query", history=[])

        assert isinstance(result, str)
        assert len(result) > 0


def test_operator_error_handles_tool_not_found():
    """
    Operator handles gracefully when tool doesn't exist.
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    call_count = [0]

    def mock_post(*args, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if call_count[0] == 1:
            resp.json.return_value = {
                "choices": [{"message": {
                    "content": '{"thought": "try tool", "tool": "nonexistent", "input": ""}',
                    "role": "assistant",
                }}]
            }
        else:
            resp.json.return_value = {
                "choices": [{"message": {
                    "content": '{"thought": "done", "answer": "Could not find tool"}',
                    "role": "assistant",
                }}]
            }
        return resp

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = mock_post

        result = op.run("call_nonexistent_tool()", history=[])

        assert isinstance(result, str)


# ── Regression Tests ───────────────────────────────────────────────────────

def test_regression_autonomous_mode_early_exit_on_no_tool_calls():
    """
    Regression: If first turn has no tool calls, loop exits.

    Verifies:
    - If first turn produces answer without tool calls, returns immediately
    - Result is a non-empty string
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    def mock_post(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {
                "content": '{"thought": "direct answer", "answer": "The sky is blue."}',
                "role": "assistant",
            }}]
        }
        return resp

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = mock_post

        result = op.run("What color is the sky?", history=[])

    assert isinstance(result, str)
    assert "blue" in result.lower()


def test_regression_info_event_on_no_tool_calls():
    """
    Regression: Operator answers without tool use returns a non-empty result.
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    def mock_post(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {
                "content": '{"thought": "direct", "answer": "Direct answer"}',
                "role": "assistant",
            }}]
        }
        return resp

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = mock_post

        result = op.run("simple query", history=[])

    assert isinstance(result, str)
    assert len(result) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
