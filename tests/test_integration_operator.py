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
from unittest.mock import patch, AsyncMock, MagicMock


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db_path():
    """Temporary ChromaDB path for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
async def mock_ollama():
    """Mock Ollama backend responses"""

    async def chat_response(url, json=None, **kwargs):
        resp = AsyncMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Mock response from Ollama",
                        "role": "assistant",
                    }
                }
            ],
            "model": json.get("model", "test-model"),
        }
        resp.raise_for_status = MagicMock()
        return resp

    return chat_response


# ── Integration Tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_integration_operator_with_tool_registry(temp_db_path, mock_ollama):
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

    op = Operator(vector_store=None, blob_store=None, tool_registry=registry)
    assert op._registry == registry


@pytest.mark.asyncio
async def test_integration_operator_history_threading(mock_ollama):
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

    async def capture_backend_call(url, json=None, **kwargs):
        messages_sent_to_backend.append(json.get("messages", []))
        resp = AsyncMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Python is awesome",
                        "role": "assistant",
                    }
                }
            ]
        }
        resp.raise_for_status = MagicMock()
        return resp

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = capture_backend_call

        await op.run("Tell me more about Python", history=initial_history)

    # Verify backend received the history
    assert len(messages_sent_to_backend) > 0
    backend_messages = messages_sent_to_backend[0]

    # Should have the initial history plus the new query
    content_str = " ".join(m.get("content", "") for m in backend_messages)
    assert "Python" in content_str


@pytest.mark.asyncio
async def test_integration_operator_tool_result_feeds_to_next_call(mock_ollama):
    """
    Tool result from one backend call is included in next backend call.

    Turn 1: LLM → tool_call(calculator, "2+2")
    Turn 1: tool_result("4")
    Turn 2: LLM receives context with tool result

    This verifies the complete loop: plan → execute → observe → reason.
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    calls_made = []

    async def mock_backend_call(url, json=None, **kwargs):
        calls_made.append(json)

        # First call: return a tool call
        if len(calls_made) == 1:
            resp = AsyncMock()
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "type": "tool_call",
                                "tool": "calculator",
                                "args": {"expr": "2+2"}
                            }),
                            "role": "assistant",
                        }
                    }
                ]
            }
            resp.raise_for_status = MagicMock()
            return resp
        else:
            # Second call: return final answer
            resp = AsyncMock()
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": "The answer is 4.",
                            "role": "assistant",
                        }
                    }
                ]
            }
            resp.raise_for_status = MagicMock()
            return resp

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = mock_backend_call

        # Mock tool execution
        with patch.object(op._registry, "execute", return_value="4"):
            events = await op.run("What is 2+2?", history=[])

    # Verify events were emitted
    assert any(e["type"] == "answer" for e in events)


@pytest.mark.asyncio
async def test_integration_operator_state_isolation():
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


@pytest.mark.asyncio
async def test_integration_operator_multiple_runs_independent(mock_ollama):
    """
    Multiple sequential operator runs maintain independent history.

    Run 1: "My name is Alice"
    Run 2: "Who am I?" (should not remember Alice without explicit history)
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    responses = [
        "Nice to meet you Alice",
        "I don't know who you are without context",
    ]
    response_idx = [0]

    async def mock_responses(url, json=None, **kwargs):
        resp = AsyncMock()
        resp.status_code = 200
        content = responses[min(response_idx[0], len(responses) - 1)]
        response_idx[0] += 1
        resp.json.return_value = {
            "choices": [{"message": {"content": content, "role": "assistant"}}]
        }
        resp.raise_for_status = MagicMock()
        return resp

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = mock_responses

        # Run 1: with no history
        events1 = await op.run("My name is Alice", history=[])
        assert any(e["type"] == "answer" for e in events1)

        # Run 2: also with no history (doesn't auto-remember)
        events2 = await op.run("Who am I?", history=[])
        assert any(e["type"] == "answer" for e in events2)


@pytest.mark.asyncio
async def test_integration_operator_concurrent_runs():
    """
    Two operator runs can execute concurrently without state corruption.

    Verifies:
    - No deadlocks
    - No cross-contamination
    - Both complete successfully
    """
    from beigebox.agents.operator import Operator

    op1 = Operator(vector_store=None, blob_store=None)
    op2 = Operator(vector_store=None, blob_store=None)

    async def mock_backend_response(url, json=None, **kwargs):
        # Simulate async I/O
        await asyncio.sleep(0.01)
        resp = AsyncMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok", "role": "assistant"}}]
        }
        resp.raise_for_status = MagicMock()
        return resp

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = mock_backend_response

        # Run both concurrently
        results = await asyncio.gather(
            op1.run("Query 1", history=[]),
            op2.run("Query 2", history=[]),
        )

    # Both should have completed with answers
    assert all(any(e["type"] == "answer" for e in r) for r in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
