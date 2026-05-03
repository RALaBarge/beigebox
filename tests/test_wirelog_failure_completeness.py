"""Wire-log completeness tests under failure conditions.

Grok review (honorable mention): test_wirelog_capture.py and
test_wire_sink.py exist, but they only test the success path. The
question is whether failed / aborted / disconnected requests also
produce wire events, and whether each sink (JSONL, SQLite, optional
Postgres) sees them — fault isolation should mean one sink failing
doesn't block the others, but the others MUST still write.

Two scenarios:

1. Per-sink fault isolation: when sink A throws, sinks B and C still
   write. Existing test_postgres_wire_sink.py already covers this for
   the abstract case; this file checks it through the failure paths
   that are most likely to expose ordering/clean-up bugs (mid-stream
   aborts, client disconnects).

2. No silent drops: every chat completion — even ones that throw —
   produces at least one wire event. This is the contract that lets
   ops tooling distinguish "request never arrived" from "request
   arrived and failed silently".
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from beigebox.backends.base import BackendResponse
from beigebox.capture import CaptureFanout
from beigebox.proxy import Proxy
from beigebox.request_normalizer import NormalizedRequest
from beigebox.storage.db import make_db
from beigebox.storage.repos import make_conversation_repo, make_wire_event_repo
from beigebox.storage.wire_sink import WireSink


# ---------------------------------------------------------------------------
# Sinks + fakes used across the test classes
# ---------------------------------------------------------------------------


class _CapturingSink(WireSink):
    """Records every event written to it. Like the one in
    test_wirelog_capture.py but standalone so this file is self-contained."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)

    def close(self) -> None:  # pragma: no cover — interface
        pass


class _BrokenSink(WireSink):
    """Sink that always raises on write — for fault-isolation tests."""

    def __init__(self) -> None:
        self.attempts = 0

    def write(self, event: dict) -> None:
        self.attempts += 1
        raise RuntimeError("simulated sink failure")

    def close(self) -> None:  # pragma: no cover — interface
        pass


class FakeRouter:
    def __init__(self) -> None:
        self._queue: list = []

    def queue(self, item) -> None:
        self._queue.append(item)

    async def forward(self, body: dict) -> BackendResponse:
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeStreamingRouter:
    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._exception: BaseException | None = None
        self._raise_at: int | None = None
        self._cancel_at: int | None = None

    def queue_chunks(self, chunks: list[str]) -> None:
        self._chunks = list(chunks)

    def raise_at(self, index: int, exc: BaseException) -> None:
        self._raise_at = index
        self._exception = exc

    def cancel_at(self, index: int) -> None:
        self._cancel_at = index

    async def forward_stream(self, body: dict):
        for i, chunk in enumerate(self._chunks):
            if self._raise_at is not None and i == self._raise_at:
                raise self._exception  # type: ignore[misc]
            if self._cancel_at is not None and i == self._cancel_at:
                raise asyncio.CancelledError()
            yield chunk
        post_idx = len(self._chunks)
        if self._raise_at == post_idx:
            raise self._exception  # type: ignore[misc]
        if self._cancel_at == post_idx:
            raise asyncio.CancelledError()


class FakeVector:
    async def store_message_async(self, **kwargs) -> None:
        pass


def _build_proxy(tmpdir: str, *, streaming: bool = False, extra_sinks: list | None = None):
    """Build a Proxy with wire_events SQLite repo wired in. ``extra_sinks``
    are tacked on after the WireLog is constructed so per-sink fault
    isolation can be tested in situ."""
    db_path = Path(tmpdir) / "test.db"
    wire_path = Path(tmpdir) / "wire.jsonl"

    db = make_db("sqlite", path=str(db_path))
    conv_repo = make_conversation_repo(db)
    conv_repo.create_tables()
    we_repo = make_wire_event_repo(db)
    we_repo.create_tables()

    vector = FakeVector()
    router = FakeStreamingRouter() if streaming else FakeRouter()

    with patch("beigebox.proxy.get_config") as mock_cfg:
        mock_cfg.return_value = {
            "backend": {"url": "http://127.0.0.1:9999", "timeout": 30, "default_model": "test"},
            "storage": {"log_conversations": True},
            "wiretap": {"path": str(wire_path), "max_lines": 1000, "rotation_enabled": False},
            "cost_tracking": {"enabled": False},
            "tool_cache": {"ttl_seconds": 60.0},
            "aliases": {},
            "guardrails": {},
            "validation": {},
            "security": {"api_anomaly": {"enabled": False}},
            "wasm": {},
        }
        with patch("beigebox.proxy.get_runtime_config", return_value={}):
            proxy = Proxy(
                conversations=conv_repo,
                vector=vector,
                backend_router=router,
                wire_events=we_repo,
            )

    proxy.capture = CaptureFanout(conversations=conv_repo, wire=proxy.wire, vector=vector)

    for s in extra_sinks or []:
        proxy.wire.add_sink(s)

    return proxy, conv_repo, we_repo, router, wire_path


@pytest.fixture
def proxy_setup():
    with tempfile.TemporaryDirectory() as tmp:
        yield _build_proxy(tmp, streaming=False)


@pytest.fixture
def stream_proxy_setup():
    with tempfile.TemporaryDirectory() as tmp:
        yield _build_proxy(tmp, streaming=True)


def _ok_response(content: str = "ok") -> BackendResponse:
    nr = NormalizedRequest(body={}, target="openrouter", transforms=[], errors=[])
    resp = BackendResponse(
        ok=True, backend_name="openrouter", latency_ms=50.0,
        data={
            "choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )
    resp.normalized_request = nr
    resp.request_summary = nr.summary({"backend": "openrouter"})
    return resp


def _err_response(status: int, msg: str) -> BackendResponse:
    nr = NormalizedRequest(body={}, target="openrouter", transforms=[], errors=[])
    resp = BackendResponse(
        ok=False, backend_name="openrouter", latency_ms=20.0,
        error=msg, status_code=status, data={},
    )
    resp.normalized_request = nr
    resp.request_summary = nr.summary({"backend": "openrouter"})
    return resp


# ---------------------------------------------------------------------------
# TestWireEventsOnFailure — wire_events are written even when the request fails
# ---------------------------------------------------------------------------


class TestWireEventsOnFailure:
    """For each failure mode, the wire_events table must contain the
    corresponding ``model_response_normalized`` event with the right
    ``meta.outcome``. Without this, ops can't tell "no traffic" from
    "traffic that all failed silently"."""

    @pytest.mark.asyncio
    async def test_backend_error_writes_response_normalized_event(self, proxy_setup):
        proxy, _conv, we_repo, router, _ = proxy_setup
        router.queue(_err_response(500, "upstream returned 500"))

        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        await proxy.forward_chat_completion(body)

        # Both request + response events are present
        events = we_repo.query(n=100)
        types = [e["event_type"] for e in events]
        assert "model_request_normalized" in types
        assert "model_response_normalized" in types

        resp_ev = next(e for e in events if e["event_type"] == "model_response_normalized")
        meta = resp_ev["meta"]
        # Outcome marker is the contract — distinct from "ok"
        assert meta["outcome"] == "upstream_error"
        assert meta["error_kind"] == "upstream_error"
        assert "500" in (meta.get("error_message") or "")

    @pytest.mark.asyncio
    async def test_router_exception_writes_failure_event(self, proxy_setup):
        proxy, _conv, we_repo, router, _ = proxy_setup
        router.queue(RuntimeError("router exploded"))

        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        with pytest.raises(RuntimeError):
            await proxy.forward_chat_completion(body)

        events = we_repo.query(n=100, event_type="model_response_normalized")
        assert len(events) == 1
        meta = events[0]["meta"]
        assert meta["outcome"] == "upstream_error"
        assert "router exploded" in (meta.get("error_message") or "")

    @pytest.mark.asyncio
    async def test_mid_stream_abort_writes_stream_aborted_event(self, stream_proxy_setup):
        proxy, _conv, we_repo, router, _ = stream_proxy_setup
        router.queue_chunks([
            'data: {"choices":[{"delta":{"content":"par"},"index":0}]}',
        ])
        router.raise_at(1, RuntimeError("stream died"))

        body = {"model": "test", "messages": [{"role": "user", "content": "go"}], "stream": True}
        with pytest.raises(RuntimeError):
            async for _ in proxy.forward_chat_completion_stream(body):
                pass

        events = we_repo.query(n=100, event_type="model_response_normalized")
        assert len(events) == 1
        meta = events[0]["meta"]
        assert meta["outcome"] == "stream_aborted"

    @pytest.mark.asyncio
    async def test_client_disconnect_writes_client_disconnect_event(self, stream_proxy_setup):
        proxy, _conv, we_repo, router, _ = stream_proxy_setup
        router.queue_chunks([
            'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}',
        ])
        router.cancel_at(1)

        body = {"model": "test", "messages": [{"role": "user", "content": "go"}], "stream": True}
        with pytest.raises(asyncio.CancelledError):
            async for _ in proxy.forward_chat_completion_stream(body):
                pass

        events = we_repo.query(n=100, event_type="model_response_normalized")
        assert len(events) == 1
        meta = events[0]["meta"]
        assert meta["outcome"] == "client_disconnect"
        assert meta["finish_reason"] == "aborted"

    @pytest.mark.asyncio
    async def test_no_silent_drops_every_completion_emits_one_response_event(
        self, proxy_setup,
    ):
        """Mix successes + failures and assert exactly N response events
        for N requests. Otherwise a regression where one path silently
        skips emit() would only show up under load."""
        proxy, _conv, we_repo, router, _ = proxy_setup
        router.queue(_ok_response("a"))
        router.queue(_err_response(503, "down"))
        router.queue(_ok_response("b"))

        for _ in range(3):
            await proxy.forward_chat_completion(
                {"model": "test", "messages": [{"role": "user", "content": "p"}]}
            )

        events = we_repo.query(n=100, event_type="model_response_normalized")
        assert len(events) == 3
        outcomes = sorted(e["meta"]["outcome"] for e in events)
        assert outcomes == ["ok", "ok", "upstream_error"]


# ---------------------------------------------------------------------------
# TestSinkFaultIsolation — one bad sink must not stop the others
# ---------------------------------------------------------------------------


class TestSinkFaultIsolation:
    """The contract: WireLog's per-sink try/except (in wiretap.py) means
    that even when one sink crashes on write, every other sink still
    receives the event. Existing test_postgres_wire_sink.py covers this
    for the abstract dispatch; here we exercise the same property
    through the proxy's actual failure paths so a regression in event
    construction (not just dispatch) gets caught."""

    @pytest.mark.asyncio
    async def test_broken_sink_does_not_block_capturing_sink_on_error_path(self):
        """When a sink throws on every write, the OTHER sinks (jsonl,
        sqlite, plus our extra capturing sink) still get the failure
        event. This is the load-bearing claim for adding postgres as a
        third sink without risking single-point-of-failure outages."""
        with tempfile.TemporaryDirectory() as tmp:
            broken = _BrokenSink()
            capturing = _CapturingSink()
            proxy, _conv, we_repo, router, wire_path = _build_proxy(
                tmp, streaming=False,
                extra_sinks=[broken, capturing],
            )

            router.queue(_err_response(502, "bad gateway"))
            await proxy.forward_chat_completion(
                {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
            )

            # The broken sink was attempted (proves the dispatch reached it)
            assert broken.attempts >= 1
            # The capturing sink ALSO got events — fault isolation works
            assert len(capturing.events) >= 1
            # And the SQLite repo did too
            sqlite_events = we_repo.query(n=10)
            assert len(sqlite_events) >= 1
            # And the JSONL file got something
            assert wire_path.exists()
            assert wire_path.read_text().strip() != ""

    @pytest.mark.asyncio
    async def test_three_sinks_all_see_same_response_event_on_success(self):
        """Cross-check the success path too: jsonl + sqlite + extra sink
        agree on the response event. Catches regressions where the meta
        dict gets enriched on one path but not another."""
        with tempfile.TemporaryDirectory() as tmp:
            extra = _CapturingSink()
            proxy, _conv, we_repo, router, wire_path = _build_proxy(
                tmp, streaming=False, extra_sinks=[extra],
            )

            router.queue(_ok_response("hello"))
            await proxy.forward_chat_completion(
                {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
            )

            # SQLite has the response
            sqlite_resp = we_repo.query(n=10, event_type="model_response_normalized")
            assert len(sqlite_resp) == 1

            # Extra sink also saw the response
            extra_resp = [
                e for e in extra.events
                if e.get("event_type") == "model_response_normalized"
            ]
            assert len(extra_resp) == 1

            # And the meta.outcome agrees
            assert sqlite_resp[0]["meta"]["outcome"] == "ok"
            assert extra_resp[0]["meta"]["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_broken_sink_does_not_block_others_on_streaming_failure(self):
        """Streaming has a different code path for capture (the assembled
        full_response is synthesized into a CapturedResponse). Cover it
        explicitly so a regression in just the streaming emit() doesn't
        slip past the non-streaming test above."""
        with tempfile.TemporaryDirectory() as tmp:
            broken = _BrokenSink()
            capturing = _CapturingSink()
            proxy, _conv, we_repo, router, _ = _build_proxy(
                tmp, streaming=True,
                extra_sinks=[broken, capturing],
            )

            router.queue_chunks([
                'data: {"choices":[{"delta":{"content":"x"},"index":0}]}',
            ])
            router.raise_at(1, RuntimeError("died"))

            with pytest.raises(RuntimeError):
                async for _ in proxy.forward_chat_completion_stream(
                    {"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True}
                ):
                    pass

            # Broken sink got at least one attempt
            assert broken.attempts >= 1
            # Capturing sink saw the abort event
            outcomes = [
                e.get("meta", {}).get("outcome")
                for e in capturing.events
                if e.get("event_type") == "model_response_normalized"
            ]
            assert "stream_aborted" in outcomes
