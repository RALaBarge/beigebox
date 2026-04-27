"""Tests for the fanout skill (beigebox.skills.fanout).

The model API is mocked at the httpx layer with httpx.MockTransport so the
tests are network-free and fast.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from beigebox.skills.fanout.pipeline import _render, fan_out


# ---------------------------------------------------------------------------
# _render template substitution
# ---------------------------------------------------------------------------


def test_render_string_item():
    assert _render("Hi {item}!", "world", 0) == "Hi world!"


def test_render_dict_item_dotted_field():
    out = _render("name={item.name} role={item.role}", {"name": "alice", "role": "sre"}, 0)
    assert out == "name=alice role=sre"


def test_render_dict_item_whole():
    out = _render("payload={item}", {"x": 1}, 0)
    # Whole-dict substitution serializes via JSON
    assert '"x": 1' in out


def test_render_index():
    assert _render("[{index}] {item}", "x", 7) == "[7] x"


def test_render_unknown_placeholder_left_literal():
    """A typo in the template doesn't kill the run — unknown keys stay literal."""
    out = _render("got {item} also {missing}", "x", 0)
    assert out == "got x also {missing}"


# ---------------------------------------------------------------------------
# fan_out — full pipeline with a mocked transport
# ---------------------------------------------------------------------------


def _mock_handler(call_log: list[dict[str, Any]], reply_for=None):
    """Build a handler that records each request and returns a canned response.

    `reply_for` may be a callable that receives the parsed body and returns the
    content string. If not given, echoes the user message back.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        call_log.append(body)
        user_msg = body["messages"][-1]["content"]
        content = reply_for(body) if reply_for else f"echo: {user_msg}"
        return httpx.Response(
            200,
            json={
                "id": "fake",
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )

    return _handler


@pytest.fixture
def patched_client(monkeypatch):
    """Replace httpx.AsyncClient with one bound to a MockTransport.

    Each test sets `state["handler"]` to control the canned response and
    inspects `state["calls"]` to assert what was sent upstream.
    """
    state: dict[str, Any] = {"calls": [], "handler": None}

    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(state["handler"])
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("beigebox.skills.fanout.pipeline.httpx.AsyncClient", _factory)
    return state


@pytest.mark.asyncio
async def test_fan_out_three_items_in_parallel(patched_client):
    patched_client["calls"] = []
    patched_client["handler"] = _mock_handler(patched_client["calls"])

    result = await fan_out(
        items=["alpha", "beta", "gamma"],
        prompt_template="say hi to {item}",
        model="x-ai/grok-4",
        concurrency=3,
    )

    assert result["stats"]["items"] == 3
    assert result["stats"]["succeeded"] == 3
    assert result["stats"]["failed"] == 0
    assert len(patched_client["calls"]) == 3
    rendered = sorted(c["messages"][-1]["content"] for c in patched_client["calls"])
    assert rendered == ["say hi to alpha", "say hi to beta", "say hi to gamma"]
    contents = sorted(r["content"] for r in result["responses"])
    assert contents == ["echo: say hi to alpha", "echo: say hi to beta", "echo: say hi to gamma"]


@pytest.mark.asyncio
async def test_fan_out_concurrency_cap(patched_client):
    """At most `concurrency` requests are in flight simultaneously."""
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def _slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    patched_client["handler"] = _slow_handler

    await fan_out(
        items=[f"x{i}" for i in range(10)],
        prompt_template="{item}",
        model="m",
        concurrency=3,
    )
    assert peak <= 3, f"peak in-flight was {peak}, expected <= 3"


@pytest.mark.asyncio
async def test_fan_out_per_item_failure_isolated(patched_client):
    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "boom" in body["messages"][-1]["content"]:
            return httpx.Response(500, json={"error": "kaboom"})
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    patched_client["handler"] = _handler

    result = await fan_out(
        items=["fine", "boom", "also-fine"],
        prompt_template="{item}",
        model="m",
        concurrency=3,
    )
    assert result["stats"]["succeeded"] == 2
    assert result["stats"]["failed"] == 1
    failed = next(r for r in result["responses"] if r["error"])
    assert "HTTPStatusError" in failed["error"] or "500" in failed["error"]


@pytest.mark.asyncio
async def test_fan_out_reduce_step_fires(patched_client):
    calls: list[dict[str, Any]] = []
    patched_client["calls"] = calls

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        user = body["messages"][-1]["content"]
        if "Merge" in user:
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "choices": [{"message": {"content": "MERGED"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": {"content": f"resp-for-{user}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    patched_client["handler"] = _handler

    result = await fan_out(
        items=["a", "b"],
        prompt_template="{item}",
        model="m",
        reduce_prompt="Merge {count} responses:\n{responses}",
    )

    assert result["reduce"] is not None
    assert result["reduce"]["content"] == "MERGED"
    # 2 fan-out calls + 1 reduce call
    assert len(calls) == 3
    reduce_call = calls[-1]
    assert "Merge 2 responses" in reduce_call["messages"][-1]["content"]
    assert "resp-for-a" in reduce_call["messages"][-1]["content"]
    assert "resp-for-b" in reduce_call["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_fan_out_reduce_skipped_on_partial_failure(patched_client):
    """By default, reduce is gated on every item succeeding."""
    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "boom" in body["messages"][-1]["content"]:
            return httpx.Response(500, json={"error": "x"})
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    patched_client["handler"] = _handler

    result = await fan_out(
        items=["fine", "boom"],
        prompt_template="{item}",
        model="m",
        reduce_prompt="merge {responses}",
    )
    assert result["reduce"] is None


@pytest.mark.asyncio
async def test_fan_out_reduce_on_partial_runs(patched_client):
    seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        seen.append(user)
        if "boom" in user:
            return httpx.Response(500, json={"error": "x"})
        if "merge" in user:
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "choices": [{"message": {"content": "MERGED-PARTIAL"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": {"content": f"OK-{user}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    patched_client["handler"] = _handler

    result = await fan_out(
        items=["fine", "boom"],
        prompt_template="{item}",
        model="m",
        reduce_prompt="merge {responses}",
        reduce_on_partial=True,
    )
    assert result["reduce"] is not None
    assert result["reduce"]["content"] == "MERGED-PARTIAL"


@pytest.mark.asyncio
async def test_fan_out_empty_items():
    result = await fan_out(items=[], prompt_template="{item}", model="m")
    assert result["stats"]["items"] == 0
    assert result["responses"] == []
    assert result["reduce"] is None


@pytest.mark.asyncio
async def test_fan_out_rejects_zero_concurrency():
    with pytest.raises(ValueError):
        await fan_out(items=["a"], prompt_template="{item}", model="m", concurrency=0)


@pytest.mark.asyncio
async def test_fan_out_token_totals_aggregated(patched_client):
    patched_client["handler"] = _mock_handler([])
    result = await fan_out(
        items=["a", "b", "c"],
        prompt_template="{item}",
        model="m",
    )
    # Mock returns prompt=10, completion=20 per call; 3 calls
    assert result["stats"]["total_prompt_tokens"] == 30
    assert result["stats"]["total_completion_tokens"] == 60
