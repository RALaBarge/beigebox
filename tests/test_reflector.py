"""Tests for beigebox.agents.reflector.Reflector."""
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from beigebox.agents.reflector import Reflector


# ── from_config disabled ──────────────────────────────────────────────────────

def test_from_config_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        "beigebox.agents.reflector.get_config",
        lambda: {"operator": {"reflection": {"enabled": False}}},
    )
    monkeypatch.setattr("beigebox.agents.reflector.get_runtime_config", lambda: {})
    r = Reflector.from_config()
    assert not r.enabled


def test_from_config_enabled(monkeypatch):
    monkeypatch.setattr(
        "beigebox.agents.reflector.get_config",
        lambda: {
            "operator": {"reflection": {"enabled": True, "model": "qwen:0.5b", "timeout": 10}},
            "backend": {"url": "http://localhost:11434", "default_model": "llama3"},
        },
    )
    monkeypatch.setattr("beigebox.agents.reflector.get_runtime_config", lambda: {})
    r = Reflector.from_config()
    assert r.enabled
    assert r._model == "qwen:0.5b"
    assert r._timeout == 10


# ── consume_insight returns None when no task ─────────────────────────────────

def test_consume_no_task():
    r = Reflector("model", "http://localhost:11434")
    assert r.consume_insight() is None


def test_consume_returns_none_when_disabled():
    r = Reflector.__new__(Reflector)
    r._enabled = False
    r._insight = None
    r._task = None
    assert r.consume_insight() is None


# ── reflect_async + consume_insight ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_reflect_async_stores_insight():
    r = Reflector("model", "http://localhost:11434", timeout=5)

    async def fake_do_reflect(*args, **kwargs):
        r._insight = "Use pathlib instead of os.path for cleaner code."

    with patch.object(r, "_do_reflect", side_effect=fake_do_reflect):
        await r.reflect_async("answer text", "context", "step 1")
        await r._task  # wait for task to complete

    insight = r.consume_insight()
    assert insight == "Use pathlib instead of os.path for cleaner code."
    # Consumed — next call should return None
    assert r.consume_insight() is None


@pytest.mark.asyncio
async def test_consume_returns_none_while_task_running():
    r = Reflector("model", "http://localhost:11434", timeout=5)

    event = asyncio.Event()

    async def slow_reflect(*args, **kwargs):
        await event.wait()
        r._insight = "insight"

    with patch.object(r, "_do_reflect", side_effect=slow_reflect):
        await r.reflect_async("ans", "ctx", "step")
        # Task is still running — consume should return None
        assert r.consume_insight() is None
        event.set()
        await r._task


@pytest.mark.asyncio
async def test_reflect_async_cancels_previous_task():
    r = Reflector("model", "http://localhost:11434", timeout=5)
    cancelled = []

    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with patch.object(r, "_do_reflect", side_effect=slow):
        await r.reflect_async("ans1", "ctx1", "step1")
        task1 = r._task
        await r.reflect_async("ans2", "ctx2", "step2")
        await asyncio.sleep(0)  # allow cancellation to propagate

    assert task1.cancelled() or cancelled


@pytest.mark.asyncio
async def test_do_reflect_handles_error_gracefully():
    r = Reflector("model", "http://localhost:11434", timeout=2)
    with patch("beigebox.agents.reflector.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=Exception("network error")
        )
        # Should not raise
        await r._do_reflect("answer", "context", "step")
    assert r._insight is None


@pytest.mark.asyncio
async def test_reflect_noop_when_disabled():
    r = Reflector.__new__(Reflector)
    r._enabled = False
    r._insight = None
    r._task = None
    await r.reflect_async("ans", "ctx", "step")
    assert r._task is None
