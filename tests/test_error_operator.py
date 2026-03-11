"""
Error scenario tests for operator.

Tests graceful failure handling:
- Backend timeouts
- Tool not found
- Malformed responses
- Rate limiting
- Network errors

Run: pytest tests/test_error_operator.py -v
"""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx


# ── Error Scenario Tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_operator_error_backend_timeout():
    """
    Backend timeout → operator emits error event, doesn't crash.

    Simulates: Ollama takes >30s and times out
    Expects: Error event with timeout message
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = asyncio.TimeoutError(
            "Backend took >30 seconds"
        )

        events = await op.run("test query", history=[])

        # Must have error event
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) > 0

        # Error message should mention timeout
        error_msg = error_events[0]["message"].lower()
        assert "timeout" in error_msg or "took" in error_msg


@pytest.mark.asyncio
async def test_operator_error_connection_refused():
    """
    Backend connection refused → operator handles gracefully.

    Simulates: Network unreachable, connection refused
    Expects: Error event, clear error message
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError(
            "Failed to establish connection"
        )

        events = await op.run("query", history=[])

        # Should have error event
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) > 0

        # Not a crash (would raise uncaught exception)
        assert isinstance(events, list)


@pytest.mark.asyncio
async def test_operator_error_malformed_response():
    """
    Backend returns invalid JSON → operator handles gracefully.

    Simulates: Backend returns garbage data
    Expects: Error event, not crash
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    async def bad_response(url, **kwargs):
        resp = AsyncMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("Invalid JSON")
        resp.raise_for_status = MagicMock()
        return resp

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = bad_response

        events = await op.run("query", history=[])

        # Should have error event
        assert any(e["type"] == "error" for e in events)


@pytest.mark.asyncio
async def test_operator_error_backend_http_error():
    """
    Backend returns HTTP 5xx error → operator handles gracefully.

    Simulates: Ollama returns 503 Service Unavailable
    Expects: Error event with HTTP status info
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    async def error_response(url, **kwargs):
        resp = AsyncMock()
        resp.status_code = 503
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Service unavailable",
            request=MagicMock(),
            response=resp,
        )
        return resp

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = error_response

        events = await op.run("query", history=[])

        # Should have error event
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) > 0


@pytest.mark.asyncio
async def test_operator_error_tool_not_found():
    """
    Tool doesn't exist in registry → operator handles gracefully.

    Simulates: LLM asks to call non-existent tool
    Expects: Error event, continues or stops gracefully
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    with patch.object(op._registry, "execute") as mock_execute:
        mock_execute.side_effect = KeyError("Tool 'nonexistent' not found")

        async def mock_backend(url, json=None, **kwargs):
            resp = AsyncMock()
            resp.status_code = 200
            # Return a tool call to non-existent tool
            resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": '{"tool": "nonexistent", "args": {}}',
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
            mock_client.post.side_effect = mock_backend

            events = await op.run("call nonexistent tool", history=[])

            # Should handle error gracefully (not crash)
            assert any(e["type"] in ["error", "answer"] for e in events)


@pytest.mark.asyncio
async def test_operator_error_tool_timeout():
    """
    Tool execution times out → operator handles gracefully.

    Simulates: Tool takes >30 seconds
    Expects: Error event, not infinite loop
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    with patch.object(op._registry, "execute") as mock_execute:
        mock_execute.side_effect = asyncio.TimeoutError("Tool execution took >30s")

        async def mock_backend(url, json=None, **kwargs):
            resp = AsyncMock()
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": '{"tool": "slow_tool", "args": {}}',
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
            mock_client.post.side_effect = mock_backend

            events = await op.run("run slow tool", history=[])

            # Should handle timeout gracefully
            assert any(e["type"] in ["error", "answer"] for e in events)


@pytest.mark.asyncio
async def test_operator_error_empty_response():
    """
    Backend returns empty message → operator handles gracefully.

    Simulates: Backend returns valid JSON but no content
    Expects: Handles gracefully, either error or empty answer
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    async def empty_response(url, **kwargs):
        resp = AsyncMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [
                {"message": {"content": "", "role": "assistant"}}  # Empty!
            ]
        }
        resp.raise_for_status = MagicMock()
        return resp

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = empty_response

        events = await op.run("test", history=[])

        # Should handle empty response without crash
        assert isinstance(events, list)


@pytest.mark.asyncio
async def test_operator_error_max_iterations_exceeded():
    """
    Operator hits max_iterations limit → stops gracefully.

    Verifies:
    - Doesn't loop infinitely
    - Returns answer or error
    - Respects iteration limit
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None, max_iterations=1)

    call_count = [0]

    async def tool_call_response(url, json=None, **kwargs):
        call_count[0] += 1
        resp = AsyncMock()
        resp.status_code = 200
        # Always return a tool call (would loop forever without iteration limit)
        resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"tool": "calculator", "args": {"expr": "1+1"}}',
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
        mock_client.post.side_effect = tool_call_response

        with patch.object(op._registry, "execute", return_value="2"):
            events = await op.run("calculate 1+1", history=[])

    # Should have stopped (call_count <= max_iterations)
    assert call_count[0] <= 2  # 1 iteration + 1 extra buffer
    assert isinstance(events, list)


@pytest.mark.asyncio
async def test_operator_error_invalid_history_format():
    """
    Invalid history format → operator handles gracefully.

    Simulates: malformed history from client
    Expects: Error or validates input
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    async def mock_backend(url, json=None, **kwargs):
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
        mock_client.post.side_effect = mock_backend

        # Invalid: missing role
        bad_history = [{"content": "test"}]

        try:
            events = await op.run("test", history=bad_history)
            # If it doesn't raise, should at least return events
            assert isinstance(events, list)
        except (ValueError, KeyError, TypeError):
            # Valid to raise on malformed input
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
