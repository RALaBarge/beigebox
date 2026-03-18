"""
Tests for observability egress (beigebox/observability/egress.py).

Covers:
  1. Batching — 150 events split into two batches (100 + 50)
  2. Retry on 429 + eventual success
  3. Fire-and-forget doesn't block (emit returns immediately)
  4. Non-retryable errors are dropped without retrying
  5. Queue-full behaviour — drops events gracefully, doesn't raise
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from beigebox.observability.egress import (
    WebhookEgress,
    build_egress_hooks,
    emit_all,
    start_egress_hooks,
    stop_egress_hooks,
)
from beigebox.utils.retry import RetryConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_egress(
    url: str = "http://test.example.com/ingest",
    batch_size: int = 100,
    batch_timeout_ms: int = 200,
    max_retries: int = 2,
) -> WebhookEgress:
    return WebhookEgress(
        url=url,
        batch_size=batch_size,
        batch_timeout_ms=batch_timeout_ms,
        retry_config=RetryConfig(max_retries=max_retries, backoff_base=0.01, backoff_max=0.05),
    )


def _mock_ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    return resp


def _mock_error_response(status: int, retry_after: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"retry-after": retry_after} if retry_after else {}
    return resp


# ---------------------------------------------------------------------------
# Test 1: Batching — 150 events → two batches (100 + 50)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batching_150_events_two_batches():
    """
    Emit 150 events with batch_size=100.
    Expect _send_with_retry called twice: first with 100 events, then with 50.
    """
    egress = _make_egress(batch_size=100, batch_timeout_ms=200)

    call_batches: list[list[dict[str, Any]]] = []

    async def fake_send(batch):
        call_batches.append(list(batch))

    egress._send_with_retry = fake_send  # type: ignore[method-assign]

    await egress.start()

    for i in range(150):
        await egress.emit({"event_id": i, "type": "test"})

    # Allow the worker to drain — wait up to 3s
    deadline = time.monotonic() + 3.0
    while sum(len(b) for b in call_batches) < 150:
        if time.monotonic() > deadline:
            break
        await asyncio.sleep(0.05)

    await egress.stop()

    total_events = sum(len(b) for b in call_batches)
    assert total_events == 150, f"Expected 150 delivered, got {total_events}"

    # Should be exactly 2 batches: one of 100, one of 50
    assert len(call_batches) == 2, (
        f"Expected 2 batches, got {len(call_batches)}: {[len(b) for b in call_batches]}"
    )
    batch_sizes = sorted(len(b) for b in call_batches)
    assert batch_sizes == [50, 100], f"Unexpected batch sizes: {batch_sizes}"


# ---------------------------------------------------------------------------
# Test 2: Retry on 429 + eventual success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_on_429_eventual_success():
    """
    First two delivery attempts return 429 (rate-limited); third succeeds.
    Verify the batch is ultimately delivered and exactly 3 httpx calls are made.
    """
    egress = _make_egress(batch_size=10, batch_timeout_ms=100, max_retries=2)

    attempt_counter = {"n": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, json):
            attempt_counter["n"] += 1
            if attempt_counter["n"] < 3:
                return _mock_error_response(429, retry_after="0")
            return _mock_ok_response()

    with patch("beigebox.observability.egress.httpx.AsyncClient", return_value=FakeClient()):
        batch = [{"id": i} for i in range(5)]
        await egress._send_with_retry(batch)

    assert attempt_counter["n"] == 3, (
        f"Expected 3 attempts (2 failures + 1 success), got {attempt_counter['n']}"
    )


# ---------------------------------------------------------------------------
# Test 3: Fire-and-forget — emit is non-blocking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_is_non_blocking():
    """
    emit() must return almost instantly even if the worker is busy.
    We measure wall-clock time for 50 emit() calls; if it blocks it would
    sleep for batch_timeout_ms per event.
    """
    # Use a very long batch_timeout to ensure the worker can't drain before we measure
    egress = _make_egress(batch_size=1000, batch_timeout_ms=10_000)

    async def noop_send(batch):
        await asyncio.sleep(100)  # simulate a very slow send

    egress._send_with_retry = noop_send  # type: ignore[method-assign]
    await egress.start()

    t0 = time.monotonic()
    for i in range(50):
        await egress.emit({"id": i})
    elapsed = time.monotonic() - t0

    await egress.stop()

    # All 50 emits should finish in well under 1 second (no blocking)
    assert elapsed < 1.0, f"emit() appears to be blocking: {elapsed:.3f}s for 50 calls"


# ---------------------------------------------------------------------------
# Test 4: Non-retryable error — batch dropped after single attempt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_retryable_error_drops_batch():
    """
    A 400 response is non-retryable. The batch should be dropped after one
    attempt — no retries.
    """
    egress = _make_egress(max_retries=3)

    attempt_counter = {"n": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, json):
            attempt_counter["n"] += 1
            return _mock_error_response(400)

    with patch("beigebox.observability.egress.httpx.AsyncClient", return_value=FakeClient()):
        batch = [{"id": 1}]
        await egress._send_with_retry(batch)

    assert attempt_counter["n"] == 1, (
        f"Non-retryable 400 should only be tried once, tried {attempt_counter['n']} times"
    )


# ---------------------------------------------------------------------------
# Test 5: Queue full — drops gracefully without raising
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_full_drops_gracefully():
    """
    Filling the queue beyond MAXSIZE should log a warning and drop events
    rather than raising or blocking.
    """
    from beigebox.observability.egress import _QUEUE_MAXSIZE

    egress = _make_egress(batch_size=_QUEUE_MAXSIZE + 1000, batch_timeout_ms=60_000)
    # Don't start the worker — queue fills without draining

    # Fill the queue to capacity
    for i in range(_QUEUE_MAXSIZE):
        await egress.emit({"id": i})

    assert egress._queue.full()

    # This should NOT raise — should drop and log a warning
    try:
        await egress.emit({"id": "overflow"})
    except Exception as exc:
        pytest.fail(f"emit() raised on full queue: {exc}")

    # Queue size should still be at max (overflow event dropped)
    assert egress._queue.qsize() == _QUEUE_MAXSIZE


# ---------------------------------------------------------------------------
# Test 6: build_egress_hooks factory
# ---------------------------------------------------------------------------

def test_build_egress_hooks_empty():
    """No webhooks configured → empty list."""
    hooks = build_egress_hooks({})
    assert hooks == []


def test_build_egress_hooks_no_url():
    """Webhook entry with no url is skipped."""
    cfg = {"observability": {"egress": {"webhooks": [{"batch_size": 10}]}}}
    hooks = build_egress_hooks(cfg)
    assert hooks == []


def test_build_egress_hooks_creates_instances():
    """Valid config produces the right number of WebhookEgress instances."""
    cfg = {
        "observability": {
            "egress": {
                "webhooks": [
                    {"url": "http://sink-a.com/ingest", "batch_size": 50},
                    {"url": "http://sink-b.com/ingest", "batch_size": 100, "method": "put"},
                ]
            }
        }
    }
    hooks = build_egress_hooks(cfg)
    assert len(hooks) == 2
    assert hooks[0].url == "http://sink-a.com/ingest"
    assert hooks[0].batch_size == 50
    assert hooks[1].method == "put"


# ---------------------------------------------------------------------------
# Test 7: emit_all delivers to all hooks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_all_delivers_to_all_hooks():
    """emit_all should call emit() on every hook in the list."""
    hooks = [WebhookEgress(url="http://a.com"), WebhookEgress(url="http://b.com")]
    received: list[tuple[str, dict]] = []

    async def recording_emit(hook_url):
        async def _emit(event):
            received.append((hook_url, event))
        return _emit

    for hook in hooks:
        hook.emit = await recording_emit(hook.url)  # type: ignore[method-assign]

    event = {"type": "test", "payload": 42}
    await emit_all(hooks, event)

    assert len(received) == 2
    urls = {r[0] for r in received}
    assert "http://a.com" in urls
    assert "http://b.com" in urls
    for _, evt in received:
        assert evt == event
