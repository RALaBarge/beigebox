"""
Tests for orchestrator (parallel LLM tasks).
Run with: pytest tests/test_orchestrator.py
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from beigebox.orchestrator import ParallelOrchestrator


@pytest.fixture
def orchestrator():
    """Create orchestrator with test config."""
    with patch("beigebox.orchestrator.get_config") as mock_cfg:
        mock_cfg.return_value = {
            "backend": {"url": "http://fake:11434"},
        }
        return ParallelOrchestrator(
            backend_url="http://fake:11434",
            max_parallel_tasks=3,
            task_timeout_seconds=5,
            total_timeout_seconds=10,
        )


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_plan(orchestrator):
    """Empty plan returns error."""
    result = await orchestrator.run([])
    assert not result["success"]
    assert "Empty" in result.get("error", "")


@pytest.mark.asyncio
async def test_single_task_success(orchestrator):
    """Single task completes successfully."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "result from LLM"}}],
        "usage": {"total_tokens": 42},
    }

    with patch("beigebox.orchestrator.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        result = await orchestrator.run([
            {"model": "test", "prompt": "hello"}
        ])

    assert result["success"]
    assert result["tasks_completed"] == 1
    assert result["tasks_failed"] == 0
    assert result["results"][0]["content"] == "result from LLM"
    assert result["results"][0]["tokens"] == 42


@pytest.mark.asyncio
async def test_multiple_tasks(orchestrator):
    """Multiple tasks run and collect results."""
    call_count = [0]

    async def fake_post(url, json=None, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": f"result {call_count[0]}"}}],
            "usage": {"total_tokens": 10},
        }
        resp.raise_for_status = MagicMock()
        return resp

    with patch("beigebox.orchestrator.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        result = await orchestrator.run([
            {"model": "code", "prompt": "task 1"},
            {"model": "large", "prompt": "task 2"},
        ])

    assert result["success"]
    assert result["tasks_completed"] == 2
    assert len(result["results"]) == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_with_empty_prompt(orchestrator):
    """Task with empty prompt returns error."""
    result = await orchestrator.run([
        {"model": "test", "prompt": ""}
    ])
    # The task itself will return an error dict
    assert result["tasks_failed"] >= 1 or len(result["errors"]) >= 1


@pytest.mark.asyncio
async def test_partial_failure(orchestrator):
    """Some tasks succeed while others fail."""
    call_count = [0]

    async def flaky_post(url, json=None, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("backend down")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 5},
        }
        resp.raise_for_status = MagicMock()
        return resp

    with patch("beigebox.orchestrator.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = flaky_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        result = await orchestrator.run([
            {"model": "test", "prompt": "will fail"},
            {"model": "test", "prompt": "will succeed"},
        ])

    # At least one succeeded
    assert result["success"]
    assert result["tasks_completed"] >= 1
    assert result["tasks_failed"] >= 1


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_parallel_cap(orchestrator):
    """Tasks beyond max_parallel are dropped."""
    plans = [{"model": "test", "prompt": f"task {i}"} for i in range(10)]

    async def always_ok(url, json=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 1},
        }
        resp.raise_for_status = MagicMock()
        return resp

    with patch("beigebox.orchestrator.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = always_ok
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        result = await orchestrator.run(plans)

    # Should be capped at 3 (max_parallel_tasks)
    assert result["tasks_completed"] <= 3


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------

def test_run_sync_valid_json(orchestrator):
    """run_sync accepts JSON and returns JSON."""
    with patch.object(orchestrator, "run", new_callable=lambda: lambda self=None, plan=None: __import__("asyncio").coroutine(lambda: {"success": True, "results": []})()) as _:
        # Just test JSON parsing
        result_str = orchestrator.run_sync("not valid json {{{")
        result = json.loads(result_str)
        assert "error" in result


def test_run_sync_not_array(orchestrator):
    """run_sync rejects non-array input."""
    result_str = orchestrator.run_sync('{"model": "test"}')
    result = json.loads(result_str)
    assert "error" in result
    assert "array" in result["error"].lower()
