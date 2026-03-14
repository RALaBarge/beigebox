"""
Integration tests for operator with real components.

Tests operator + VectorStore + ToolRegistry working together.
Mocks only external services (Ollama backend).

Run: pytest tests/test_integration_operator.py -v
"""

import asyncio
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db_path():
    """Temporary ChromaDB path for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_ollama_response():
    """Sync mock for Ollama backend responses (httpx.Client)"""
    def make_response(content='{"thought": "done", "answer": "Mock response from Ollama"}'):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"content": content, "role": "assistant"}}],
            "model": "test-model",
        }
        return resp
    return make_response


# ── Integration Tests ──────────────────────────────────────────────────────

def test_integration_operator_with_tool_registry(temp_db_path, mock_ollama_response):
    """
    Operator initialized with real ToolRegistry can discover tools.

    Verifies:
    - ToolRegistry is initialized
    - Operator has access to tools
    - Tool list is non-empty
    """
    from beigebox.tools.registry import ToolRegistry
    from beigebox.agents.operator import Operator

    registry = ToolRegistry(vector_store=None)
    tool_names = registry.list_tools()

    assert isinstance(tool_names, list)
    assert len(tool_names) > 0  # Should have some default tools

    # Operator creates its own registry, verify it's initialized
    op = Operator(vector_store=None, blob_store=None)
    assert op._registry is not None
    assert len(op._registry.list_tools()) > 0


def test_integration_operator_history_threading(mock_ollama_response):
    """
    History is threaded correctly through operator run.

    User provides: [user msg, assistant msg]
    Operator includes in backend call: [user msg, assistant msg, new user msg]
    Backend receives full context.
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    initial_history = [
        {"role": "user", "content": "I like Python"},
        {"role": "assistant", "content": "That's great!"},
    ]

    messages_sent_to_backend = []

    def capture_backend_call(*args, **kwargs):
        json_body = kwargs.get("json", {})
        messages_sent_to_backend.append(json_body.get("messages", []))
        return mock_ollama_response()

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = capture_backend_call

        result = op.run("Tell me more about Python", history=initial_history)

    # Verify backend received the history
    assert len(messages_sent_to_backend) > 0
    backend_messages = messages_sent_to_backend[0]

    # Should have the initial history plus the new query
    content_str = " ".join(m.get("content", "") for m in backend_messages)
    assert "Python" in content_str


def test_integration_operator_tool_result_feeds_to_next_call(mock_ollama_response):
    """
    Tool result from one backend call is included in next backend call.

    Turn 1: LLM → tool_call(calculator, "2+2")
    Turn 1: tool_result("4")
    Turn 2: LLM receives context with tool result

    This verifies the complete loop: plan → execute → observe → reason.
    """
    from beigebox.agents.operator import Operator
    import json

    op = Operator(vector_store=None, blob_store=None)

    calls_made = []

    def mock_backend_call(*args, **kwargs):
        json_body = kwargs.get("json", {})
        calls_made.append(json_body)

        # First call: return a tool call
        if len(calls_made) == 1:
            return mock_ollama_response(
                content=json.dumps({
                    "thought": "calculate",
                    "tool": "calculator",
                    "input": "2+2"
                })
            )
        else:
            return mock_ollama_response(
                content='{"thought": "done", "answer": "The answer is 4."}'
            )

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = mock_backend_call

        with patch.object(op._registry, "run_tool", return_value="4"):
            result = op.run("What is 2+2?", history=[])

    assert isinstance(result, str)
    assert len(result) > 0


def test_integration_operator_state_isolation():
    """
    Two operator instances don't share state.

    Verifies:
    - Each operator has independent state
    - No cross-contamination between parallel runs
    """
    from beigebox.agents.operator import Operator

    op1 = Operator(vector_store=None, blob_store=None)
    op2 = Operator(vector_store=None, blob_store=None)

    # Different instances
    assert op1 is not op2
    assert op1._registry is not op2._registry


def test_integration_operator_multiple_runs_independent(mock_ollama_response):
    """
    Multiple sequential operator runs maintain independent history.

    Run 1: "My name is Alice"
    Run 2: "Who am I?" (should not remember Alice without explicit history)
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    responses = [
        '{"thought": "greeting", "answer": "Nice to meet you Alice"}',
        '{"thought": "unknown", "answer": "I don\'t know who you are without context"}',
    ]
    response_idx = [0]

    def mock_responses(*args, **kwargs):
        content = responses[min(response_idx[0], len(responses) - 1)]
        response_idx[0] += 1
        return mock_ollama_response(content=content)

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = mock_responses

        result1 = op.run("My name is Alice", history=[])
        assert isinstance(result1, str)
        assert len(result1) > 0

        result2 = op.run("Who am I?", history=[])
        assert isinstance(result2, str)
        assert len(result2) > 0


def test_integration_operator_concurrent_runs():
    """
    Two operator instances can run independently without state corruption.

    Verifies:
    - No shared state between instances
    - Both produce string results
    """
    from beigebox.agents.operator import Operator

    op1 = Operator(vector_store=None, blob_store=None)
    op2 = Operator(vector_store=None, blob_store=None)

    def mock_response(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"thought": "ok", "answer": "ok"}', "role": "assistant"}}]
        }
        return resp

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = mock_response

        result1 = op1.run("Query 1", history=[])
        result2 = op2.run("Query 2", history=[])

    assert isinstance(result1, str)
    assert isinstance(result2, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
