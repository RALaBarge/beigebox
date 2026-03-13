"""Tests for beigebox.agents.shadow.ShadowAgent."""
import asyncio
import pytest
from unittest.mock import patch, MagicMock
from beigebox.agents.shadow import ShadowAgent, _words


# ── _words helper ─────────────────────────────────────────────────────────────

def test_words_extracts_lowercase():
    assert _words("Hello World") == {"hello", "world"}

def test_words_filters_short():
    # Only words ≥ 3 chars
    assert "an" not in _words("an API endpoint")
    assert "api" in _words("an API endpoint")


# ── ShadowAgent.diverges ──────────────────────────────────────────────────────

def test_diverges_identical():
    assert not ShadowAgent.diverges("build a REST API", "build a REST API")

def test_diverges_totally_different():
    assert ShadowAgent.diverges(
        "build a python REST API server with Flask",
        "use GraphQL with TypeScript and Express framework",
    )

def test_diverges_partial_overlap():
    # Shares some words but enough different
    assert ShadowAgent.diverges(
        "implement a caching layer with Redis for the API",
        "use in-memory LRU cache instead of Redis for the API",
    )

def test_diverges_empty_strings():
    assert not ShadowAgent.diverges("", "something")
    assert not ShadowAgent.diverges("something", "")
    assert not ShadowAgent.diverges("", "")

def test_diverges_threshold():
    # diverges = similarity < (1 - threshold)
    # threshold=1.0 → check similarity < 0.0, impossible → never diverges
    assert not ShadowAgent.diverges("hello world", "goodbye world", threshold=1.0)
    # "hello world" vs "goodbye world": Jaccard = 1/3 ≈ 0.33
    # threshold=0.5 → check 0.33 < 0.5 → True
    assert ShadowAgent.diverges("hello world", "goodbye world", threshold=0.5)
    # threshold=0.0 → check 0.33 < 1.0 → True (any non-identical text diverges)
    assert ShadowAgent.diverges("hello world", "goodbye world", threshold=0.0)


# ── from_config disabled ──────────────────────────────────────────────────────

def test_from_config_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        "beigebox.agents.shadow.get_config",
        lambda: {"harness": {"shadow_agents": {"enabled": False}}},
    )
    monkeypatch.setattr("beigebox.agents.shadow.get_runtime_config", lambda: {})
    s = ShadowAgent.from_config()
    assert not s.enabled


def test_from_config_enabled(monkeypatch):
    monkeypatch.setattr(
        "beigebox.agents.shadow.get_config",
        lambda: {
            "harness": {"shadow_agents": {
                "enabled": True, "model": "qwen:0.5b",
                "timeout": 20, "max_tool_calls": 2,
                "divergence_threshold": 0.4,
            }},
            "backend": {"url": "http://localhost:11434", "default_model": "llama3"},
        },
    )
    monkeypatch.setattr("beigebox.agents.shadow.get_runtime_config", lambda: {})
    s = ShadowAgent.from_config()
    assert s.enabled
    assert s._model == "qwen:0.5b"
    assert s._max_tool_calls == 2
    assert s._divergence_threshold == 0.4


# ── run_shadow returns None on failure ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shadow_returns_none_on_error():
    s = ShadowAgent("model", "http://localhost:11434", timeout=1)
    with patch("beigebox.agents.operator.Operator") as MockOp:
        MockOp.side_effect = Exception("import error")
        result = await s.run_shadow("build something", None)
    assert result is None


@pytest.mark.asyncio
async def test_run_shadow_timeout():
    s = ShadowAgent("model", "http://localhost:11434", timeout=1)

    async def slow_run(question, history):
        await asyncio.sleep(10)
        return "answer"

    with patch("beigebox.agents.operator.Operator") as MockOp:
        mock_op = MagicMock()
        mock_op.run.side_effect = lambda q, history: __import__("time").sleep(10) or "ans"
        MockOp.return_value = mock_op
        result = await s.run_shadow("build something", None)
    assert result is None  # timed out


# ── collect ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_returns_result():
    s = ShadowAgent("model", "http://localhost:11434")

    async def done():
        return "shadow answer"

    task = asyncio.ensure_future(done())
    await asyncio.sleep(0)
    result = await s.collect(task, wait=1.0)
    assert result == "shadow answer"


@pytest.mark.asyncio
async def test_collect_timeout_returns_none():
    s = ShadowAgent("model", "http://localhost:11434")

    async def slow():
        await asyncio.sleep(10)
        return "answer"

    task = asyncio.ensure_future(slow())
    result = await s.collect(task, wait=0.05)
    assert result is None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── max_tool_calls wired to Operator ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_shadow_passes_max_tool_calls():
    s = ShadowAgent("model", "http://localhost:11434", timeout=5, max_tool_calls=2)
    captured = {}

    with patch("beigebox.agents.operator.Operator") as MockOp:
        mock_op = MagicMock()
        mock_op.run.return_value = "shadow result"
        MockOp.return_value = mock_op
        MockOp.side_effect = lambda **kw: (captured.update(kw), mock_op)[1]
        result = await s.run_shadow("implement something", None)

    assert captured.get("max_tool_calls") == 2
