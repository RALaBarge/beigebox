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

import pytest
from unittest.mock import patch, MagicMock
import httpx


# ── Error Scenario Tests ───────────────────────────────────────────────────

def test_operator_error_backend_timeout():
    """
    Backend timeout → operator returns error string, doesn't crash.

    Simulates: Ollama takes >30s and times out
    Expects: Error string mentioning unavailability
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    with patch("httpx.Client") as mock_client_class, \
         patch("time.sleep"):  # skip retry sleeps
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = TimeoutError("Backend took >30 seconds")

        result = op.run("test query", history=[])

        assert isinstance(result, str)
        assert len(result) > 0
        # Should mention unavailability or timeout
        assert "unavailable" in result.lower() or "timeout" in result.lower() or "timed" in result.lower()


def test_operator_error_connection_refused():
    """
    Backend connection refused → operator handles gracefully.

    Simulates: Network unreachable, connection refused
    Expects: Error string, no crash
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    with patch("httpx.Client") as mock_client_class, \
         patch("time.sleep"):
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError(
            "Failed to establish connection"
        )

        result = op.run("query", history=[])

        assert isinstance(result, str)
        assert len(result) > 0


def test_operator_error_malformed_response():
    """
    Backend returns invalid JSON → operator handles gracefully.

    Simulates: Backend returns garbage data
    Expects: Error string or raw response, not crash
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "not json at all", "role": "assistant"}}]
    }

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = op.run("query", history=[])

        # Should not crash — returns raw text or error
        assert isinstance(result, str)


def test_operator_error_backend_http_error():
    """
    Backend returns HTTP 5xx error → operator handles gracefully.

    Simulates: Ollama returns 503 Service Unavailable
    Expects: Error string with HTTP info
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Service unavailable",
        request=MagicMock(),
        response=mock_resp,
    )

    with patch("httpx.Client") as mock_client_class, \
         patch("time.sleep"):
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = op.run("query", history=[])

        assert isinstance(result, str)
        assert len(result) > 0


def test_operator_error_tool_not_found():
    """
    Tool doesn't exist in registry → operator handles gracefully.

    The tool loop handles unknown tool names in _run_tool — returns error observation,
    model then generates a final answer.
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    # Return a tool call to non-existent tool on first call, answer on second
    call_count = [0]

    def mock_post(*args, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if call_count[0] == 1:
            resp.json.return_value = {
                "choices": [{"message": {
                    "content": '{"thought": "trying tool", "tool": "nonexistent", "input": ""}',
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

        result = op.run("call nonexistent tool", history=[])

        # Should handle error gracefully
        assert isinstance(result, str)


def test_operator_error_empty_response():
    """
    Backend returns empty message → operator handles gracefully.

    Simulates: Backend returns valid JSON but no content
    Expects: Handles gracefully, returns string
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
                "choices": [{"message": {"content": "", "role": "assistant"}}]
            }
        else:
            resp.json.return_value = {
                "choices": [{"message": {
                    "content": '{"thought": "done", "answer": "No content provided"}',
                    "role": "assistant",
                }}]
            }
        return resp

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = mock_post

        result = op.run("test", history=[])

        # Should handle empty response without crash
        assert isinstance(result, str)


def test_operator_error_max_iterations_exceeded():
    """
    Operator hits max_iterations limit → stops gracefully.

    Verifies:
    - Doesn't loop infinitely
    - Returns answer or error
    - Respects iteration limit
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None, max_iterations_override=2)

    call_count = [0]

    def mock_post(*args, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        # Always return a tool call (would loop forever without iteration limit)
        resp.json.return_value = {
            "choices": [{"message": {
                "content": '{"thought": "trying", "tool": "calculator", "args": {"expr": "1+1"}}',
                "role": "assistant",
            }}]
        }
        return resp

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = mock_post

        with patch.object(op._registry, "run_tool", return_value="2"):
            result = op.run("calculate 1+1", history=[])

    # Should have stopped (call_count <= max_iterations + small buffer)
    assert call_count[0] <= 4
    assert isinstance(result, str)


def test_operator_error_invalid_history_format():
    """
    Invalid history format → operator handles gracefully.

    Simulates: malformed history from client
    Expects: Error or validates input without crash
    """
    from beigebox.agents.operator import Operator

    op = Operator(vector_store=None, blob_store=None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"thought": "ok", "answer": "ok"}', "role": "assistant"}}]
    }

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        # Invalid: missing role
        bad_history = [{"content": "test"}]

        try:
            result = op.run("test", history=bad_history)
            # If it doesn't raise, should return a string
            assert isinstance(result, str)
        except (ValueError, KeyError, TypeError):
            # Valid to raise on malformed input
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
