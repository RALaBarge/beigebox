"""
Tests for HarnessOrchestrator — goal-directed multi-agent coordinator.

Covers:
  - Event stream structure (start, plan, dispatch, result, evaluate, finish)
  - Finish action terminates the loop
  - Round cap is respected
  - _run_model success and failure paths
  - _run_operator uses 127.0.0.1 (Docker-safe)
  - Error classification
  - Retry logic
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

FAKE_CFG = {
    "backend": {"url": "http://fake:11434", "default_model": "llama3.2"},
    "operator": {"model": "llama3.2"},
    "server": {"port": 8000},
    "harness": {
        "retry": {"max_retries": 1, "backoff_base": 0.1, "backoff_max": 0.2},
        "stagger": {"operator_seconds": 0.0, "model_seconds": 0.0},
        "timeouts": {"task_seconds": 5, "operator_seconds": 5},
        "store_runs": False,
    },
}


def _make_harness(targets=None):
    with patch("beigebox.agents.harness_orchestrator.get_config", return_value=FAKE_CFG):
        from beigebox.agents.harness_orchestrator import HarnessOrchestrator
        return HarnessOrchestrator(
            available_targets=targets or ["model:llama3.2"],
            model="llama3.2",
            max_rounds=3,
        )


def _mock_llm_response(content: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    resp.raise_for_status = MagicMock()
    return resp


def _patch_httpx(responses: list):
    """Patch httpx.AsyncClient.post to return responses in sequence."""
    call_idx = [0]

    async def fake_post(url, **kwargs):
        i = call_idx[0]
        call_idx[0] += 1
        if i < len(responses):
            return responses[i]
        return responses[-1]  # repeat last

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ── Event stream helpers ──────────────────────────────────────────────────────

async def _collect(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


def _events_of_type(events, t):
    return [e for e in events if e["type"] == t]


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_event_emitted():
    """First event is always 'start' with goal and targets."""
    harness = _make_harness()

    plan_json = json.dumps({
        "action": "finish",
        "tasks": [],
        "reasoning": "trivial",
        "answer": "done immediately",
    })
    finish_json = json.dumps({
        "action": "finish",
        "answer": "done",
    })

    with patch("beigebox.agents.harness_orchestrator.get_config", return_value=FAKE_CFG), \
         patch("beigebox.agents.harness_orchestrator.httpx.AsyncClient") as MockCls:
        MockCls.return_value = _patch_httpx([
            _mock_llm_response(plan_json),
            _mock_llm_response(finish_json),
        ])
        events = await _collect(harness.run("test goal"))

    start = _events_of_type(events, "start")
    assert len(start) == 1
    assert start[0]["goal"] == "test goal"
    assert "targets" in start[0]
    assert "run_id" in start[0]


@pytest.mark.asyncio
async def test_finish_action_terminates_loop():
    """Plan returning action=finish with no tasks ends in one round."""
    harness = _make_harness()

    plan_json = json.dumps({
        "action": "finish",
        "tasks": [],
        "reasoning": "nothing to do",
        "answer": "the answer is 42",
    })

    with patch("beigebox.agents.harness_orchestrator.get_config", return_value=FAKE_CFG), \
         patch("beigebox.agents.harness_orchestrator.httpx.AsyncClient") as MockCls:
        MockCls.return_value = _patch_httpx([_mock_llm_response(plan_json)])
        events = await _collect(harness.run("what is 6*7"))

    finish = _events_of_type(events, "finish")
    assert len(finish) == 1
    assert "42" in finish[0]["answer"]
    # rounds >= 0; capped only present on round-cap finish path
    assert finish[0]["rounds"] >= 0
    assert finish[0].get("capped", False) is False


@pytest.mark.asyncio
async def test_round_cap_respected():
    """If LLM never calls finish, loop stops at max_rounds."""
    harness = _make_harness(targets=["model:llama3.2"])

    # Always dispatch one task, never finish
    plan_json = json.dumps({
        "action": "dispatch",
        "tasks": [{"target": "model:llama3.2", "prompt": "do something", "rationale": ""}],
        "reasoning": "keep going",
    })
    evaluate_json = json.dumps({
        "action": "continue",
        "assessment": "not done yet",
    })
    task_result = "task output"

    with patch("beigebox.agents.harness_orchestrator.get_config", return_value=FAKE_CFG), \
         patch("beigebox.agents.harness_orchestrator.httpx.AsyncClient") as MockCls:
        MockCls.return_value = _patch_httpx([
            _mock_llm_response(plan_json),
            _mock_llm_response(task_result),
            _mock_llm_response(evaluate_json),
        ] * 10)
        events = await _collect(harness.run("infinite loop goal"))

    finish = _events_of_type(events, "finish")
    assert len(finish) == 1
    assert finish[0]["capped"] is True
    assert finish[0]["rounds"] == 3  # max_rounds=3


@pytest.mark.asyncio
async def test_result_events_emitted():
    """Result events are emitted for each dispatched task."""
    harness = _make_harness(targets=["model:llama3.2"])

    plan_json = json.dumps({
        "action": "dispatch",
        "tasks": [{"target": "model:llama3.2", "prompt": "summarize this", "rationale": ""}],
        "reasoning": "need summary",
    })
    task_resp = "here is a summary"
    evaluate_json = json.dumps({
        "action": "finish",
        "answer": "summary complete",
    })

    with patch("beigebox.agents.harness_orchestrator.get_config", return_value=FAKE_CFG), \
         patch("beigebox.agents.harness_orchestrator.httpx.AsyncClient") as MockCls:
        MockCls.return_value = _patch_httpx([
            _mock_llm_response(plan_json),
            _mock_llm_response(task_resp),
            _mock_llm_response(evaluate_json),
        ])
        events = await _collect(harness.run("summarize"))

    result_events = _events_of_type(events, "result")
    assert len(result_events) >= 1
    assert result_events[0]["content"] == "here is a summary"
    assert result_events[0]["status"] == "done"


@pytest.mark.asyncio
async def test_error_event_on_llm_exception():
    """If the planning LLM throws, an error event is emitted."""
    harness = _make_harness()

    async def bad_post(*args, **kwargs):
        raise Exception("connection refused")

    mock_client = AsyncMock()
    mock_client.post = bad_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("beigebox.agents.harness_orchestrator.get_config", return_value=FAKE_CFG), \
         patch("beigebox.agents.harness_orchestrator.httpx.AsyncClient", return_value=mock_client):
        events = await _collect(harness.run("will fail"))

    error_events = _events_of_type(events, "error")
    assert len(error_events) >= 1


# ── _run_operator uses 127.0.0.1 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_operator_uses_loopback():
    """_run_operator must call 127.0.0.1 not localhost (Docker safety)."""
    harness = _make_harness(targets=["operator"])

    captured_urls = []

    async def capture_post(url, **kwargs):
        captured_urls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "answer": "operator answer"}
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = AsyncMock()
    mock_client.post = capture_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("beigebox.agents.harness_orchestrator.httpx.AsyncClient", return_value=mock_client):
        await harness._run_operator("test query")

    assert len(captured_urls) == 1
    assert "127.0.0.1" in captured_urls[0]
    assert "localhost" not in captured_urls[0]


# ── Error classification ──────────────────────────────────────────────────────

class TestErrorClassification:
    @pytest.fixture
    def classify(self):
        with patch("beigebox.agents.harness_orchestrator.get_config", return_value=FAKE_CFG):
            from beigebox.agents.harness_orchestrator import HarnessOrchestrator
            return HarnessOrchestrator._classify_error

    def test_timeout_classified(self, classify):
        assert classify(Exception("connection timed out")) == "timeout"

    def test_connect_classified(self, classify):
        assert classify(Exception("connection refused")) == "connection"

    def test_404_classified(self, classify):
        assert classify(Exception("404 not found")) == "not_found"

    def test_429_classified(self, classify):
        assert classify(Exception("429 rate limit exceeded")) == "rate_limit"

    def test_500_classified(self, classify):
        assert classify(Exception("500 internal server error")) == "internal_error"

    def test_502_classified(self, classify):
        assert classify(Exception("502 bad gateway")) == "internal_error"

    def test_unknown_exception_classified(self, classify):
        assert classify(ValueError("something completely unexpected")) == "unknown"
