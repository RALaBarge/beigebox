"""End-to-end behavior tests for /v1/chat/completions error paths.

These tests cover the gaps Grok flagged (review #1): the proxy's main
endpoint returns the right shape AND records the right capture row when
upstream fails, when streaming is interrupted, when the client
disconnects, and when malformed inputs come in.

Pattern: same as ``test_proxy_capture_integration.py`` — build a real
``Proxy`` against an in-memory SQLite db with a ``CaptureFanout`` wired,
inject a fake backend router, and assert on both the returned response
body and the rows that landed in ``messages``. This is end-to-end as far
as the proxy is concerned (real Proxy, real ConversationRepo, real
WireLog) without booting FastAPI just to re-test the same forward call.

Why not ``httpx.AsyncClient(app=app)``? The whole router shim does is
``return JSONResponse(await proxy.forward_chat_completion(body))`` plus
auth/ACL — covered separately in test_auth.py. Hitting the FastAPI layer
adds a chromadb dependency to startup and obscures which row in
``messages`` the assertion is checking. Keep the proxy contract honest;
let the router test cover routing.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from beigebox.backends.base import BackendResponse
from beigebox.capture import CaptureFanout
from beigebox.proxy import Proxy
from beigebox.request_normalizer import NormalizedRequest
from beigebox.storage.db import make_db
from beigebox.storage.repos import make_conversation_repo


# ---------------------------------------------------------------------------
# Fakes — mirror the patterns in test_proxy_capture_integration.py
# ---------------------------------------------------------------------------


class FakeRouter:
    """Backend-router stand-in that yields whatever was queued."""

    def __init__(self) -> None:
        self._queue: list[BackendResponse | Exception] = []

    def queue(self, item) -> None:
        self._queue.append(item)

    async def forward(self, body: dict) -> BackendResponse:
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeStreamingRouter:
    """Async-generator router. ``raise_at`` / ``cancel_at`` simulate failure."""

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
        # raise_at / cancel_at can also fire AFTER all chunks (index ==
        # len(chunks)), to simulate a clean stream that errors at the end.
        post_idx = len(self._chunks)
        if self._raise_at == post_idx:
            raise self._exception  # type: ignore[misc]
        if self._cancel_at == post_idx:
            raise asyncio.CancelledError()


class FakeVector:
    """Records embed calls but doesn't actually embed."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def store_message_async(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _build_proxy(tmpdir: str, *, streaming: bool = False):
    db_path = Path(tmpdir) / "test.db"
    wire_path = Path(tmpdir) / "wire.jsonl"

    db = make_db("sqlite", path=str(db_path))
    repo = make_conversation_repo(db)
    repo.create_tables()
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
            proxy = Proxy(conversations=repo, vector=vector, backend_router=router)

    proxy.capture = CaptureFanout(conversations=repo, wire=proxy.wire, vector=vector)
    return proxy, repo, router, vector, wire_path


@pytest.fixture
def proxy_setup():
    with tempfile.TemporaryDirectory() as tmp:
        yield _build_proxy(tmp, streaming=False)


@pytest.fixture
def stream_proxy_setup():
    with tempfile.TemporaryDirectory() as tmp:
        yield _build_proxy(tmp, streaming=True)


def _all_messages(repo) -> list[dict]:
    return repo._db.fetchall("SELECT * FROM messages ORDER BY timestamp")


def _ok_response(content: str = "hi", *, cost: float | None = None) -> BackendResponse:
    nr = NormalizedRequest(body={}, target="openrouter", transforms=[], errors=[])
    resp = BackendResponse(
        ok=True,
        backend_name="openrouter",
        latency_ms=100.0,
        cost_usd=cost,
        data={
            "choices": [{
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )
    resp.normalized_request = nr
    resp.request_summary = nr.summary({"backend": "openrouter"})
    return resp


def _err_response(status_code: int, error: str) -> BackendResponse:
    nr = NormalizedRequest(body={}, target="openrouter", transforms=[], errors=[])
    resp = BackendResponse(
        ok=False,
        backend_name="openrouter",
        latency_ms=20.0,
        error=error,
        status_code=status_code,
        data={},
    )
    resp.normalized_request = nr
    resp.request_summary = nr.summary({"backend": "openrouter"})
    return resp


# ---------------------------------------------------------------------------
# TestChatCompletionsErrorHandling — non-streaming
# ---------------------------------------------------------------------------


class TestChatCompletionsErrorHandling:
    """Backend errors propagate through forward_chat_completion as a synthetic
    assistant response, AND the failure is captured to the messages table.

    The contract: a frontend never gets a raw exception — it gets either a
    real response OR a synthesized "[BeigeBox] Backend error: ..." message.
    Either way, an assistant row with the correct ``capture_outcome`` lands
    in storage so /beigebox/stats and replay still work.
    """

    @pytest.mark.asyncio
    async def test_backend_500_produces_upstream_error_row(self, proxy_setup):
        proxy, repo, router, _vec, _ = proxy_setup
        router.queue(_err_response(500, "upstream returned 500"))

        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        result = await proxy.forward_chat_completion(body)

        # Client gets a synthetic assistant message — never an exception.
        assert "Backend error" in result["choices"][0]["message"]["content"]
        assert "500" in result["choices"][0]["message"]["content"]

        # Both rows present, assistant marked as upstream_error.
        rows = _all_messages(repo)
        assert len(rows) == 2
        asst = next(r for r in rows if r["role"] == "assistant")
        assert asst["capture_outcome"] == "upstream_error"
        assert asst["error_kind"] == "upstream_error"
        assert asst["finish_reason"] == "error"
        assert "500" in (asst["error_message"] or "")

    @pytest.mark.asyncio
    async def test_backend_429_propagated_and_captured(self, proxy_setup):
        """Rate-limit responses must be captured cleanly — no orphaned request rows,
        no missing assistant row. The error_message preserves the 429 marker so
        downstream tooling (e.g. retry policy) can recognize the rate-limit case."""
        proxy, repo, router, _vec, _ = proxy_setup
        router.queue(_err_response(429, "rate_limit_exceeded: 429 Too Many Requests"))

        body = {"model": "test", "messages": [{"role": "user", "content": "go"}]}
        result = await proxy.forward_chat_completion(body)

        # Synthetic response surfaces the 429 to the client
        assert "429" in result["choices"][0]["message"]["content"]

        rows = _all_messages(repo)
        # Exactly two rows — no orphan request from a half-applied capture
        assert len(rows) == 2
        user = next(r for r in rows if r["role"] == "user")
        asst = next(r for r in rows if r["role"] == "assistant")
        # User row was captured cleanly; assistant carries the failure marker
        assert user["capture_outcome"] == "ok"
        assert asst["capture_outcome"] == "upstream_error"
        assert "429" in (asst["error_message"] or "")

    @pytest.mark.asyncio
    async def test_router_exception_still_captures_failure_row(self, proxy_setup):
        """If the backend router raises (network down, etc) the proxy re-raises
        — but a row still lands in messages so the failure isn't invisible."""
        proxy, repo, router, _vec, _ = proxy_setup
        router.queue(httpx.ConnectError("cannot connect to backend"))

        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        with pytest.raises(httpx.ConnectError):
            await proxy.forward_chat_completion(body)

        rows = _all_messages(repo)
        assert len(rows) == 2
        asst = next(r for r in rows if r["role"] == "assistant")
        assert asst["capture_outcome"] == "upstream_error"
        assert "cannot connect" in (asst["error_message"] or "")

    @pytest.mark.asyncio
    async def test_consecutive_failed_requests_no_row_leak(self, proxy_setup):
        """Two failed-then-failed-then-success requests must produce exactly
        2*3=6 rows. Catches any lingering state in the capture pipeline."""
        proxy, repo, router, _vec, _ = proxy_setup
        router.queue(_err_response(503, "service unavailable"))
        router.queue(_err_response(502, "bad gateway"))
        router.queue(_ok_response("recovered"))

        for _ in range(3):
            await proxy.forward_chat_completion(
                {"model": "test", "messages": [{"role": "user", "content": "ping"}]}
            )

        rows = _all_messages(repo)
        assert len(rows) == 6
        outcomes = sorted(r["capture_outcome"] for r in rows if r["role"] == "assistant")
        assert outcomes == ["ok", "upstream_error", "upstream_error"]


# ---------------------------------------------------------------------------
# TestChatCompletionsStreamingErrorHandling — streaming
# ---------------------------------------------------------------------------


class TestChatCompletionsStreamingErrorHandling:
    """Streaming has its own contract: partial content must be preserved no
    matter how the stream ends (success, mid-stream backend death, client
    disconnect). Each failure mode has a distinct ``capture_outcome``."""

    @pytest.mark.asyncio
    async def test_mid_stream_backend_failure_captures_partial(self, stream_proxy_setup):
        proxy, repo, router, _vec, _ = stream_proxy_setup
        router.queue_chunks([
            'data: {"choices":[{"delta":{"content":"par"},"index":0}]}',
            'data: {"choices":[{"delta":{"content":"tial"},"index":0}]}',
        ])
        router.raise_at(2, RuntimeError("connection reset by peer"))

        body = {"model": "test", "messages": [{"role": "user", "content": "go"}], "stream": True}
        with pytest.raises(RuntimeError, match="connection reset"):
            async for _ in proxy.forward_chat_completion_stream(body):
                pass

        rows = _all_messages(repo)
        asst = next(r for r in rows if r["role"] == "assistant")
        # Outcome marker is the contract; partial assembly is the proof
        # nothing was lost.
        assert asst["capture_outcome"] == "stream_aborted"
        assert "partial" in asst["content"]
        assert "connection reset" in (asst["error_message"] or "")

    @pytest.mark.asyncio
    async def test_client_disconnect_distinguished_from_backend_error(self, stream_proxy_setup):
        """``client_disconnect`` and ``stream_aborted`` are different outcomes —
        operators investigate them differently. Conflating them would hide
        flaky-frontend bugs behind backend-noise alarms."""
        proxy, repo, router, _vec, _ = stream_proxy_setup
        router.queue_chunks([
            'data: {"choices":[{"delta":{"content":"hello "},"index":0}]}',
            'data: {"choices":[{"delta":{"content":"there"},"index":0}]}',
        ])
        router.cancel_at(2)

        body = {"model": "test", "messages": [{"role": "user", "content": "go"}], "stream": True}
        with pytest.raises(asyncio.CancelledError):
            async for _ in proxy.forward_chat_completion_stream(body):
                pass

        rows = _all_messages(repo)
        asst = next(r for r in rows if r["role"] == "assistant")
        assert asst["capture_outcome"] == "client_disconnect"
        assert asst["finish_reason"] == "aborted"
        # Partial content preserved
        assert "hello" in asst["content"]

    @pytest.mark.asyncio
    async def test_stream_failure_before_first_chunk(self, stream_proxy_setup):
        """When upstream dies before yielding any chunk, the request envelope
        + a failure response envelope must still appear. Otherwise the worst
        bugs (upstream is silently dead) are also the most invisible."""
        proxy, repo, router, _vec, _ = stream_proxy_setup
        router.queue_chunks([])
        router.raise_at(0, RuntimeError("upstream never spoke"))

        body = {"model": "test", "messages": [{"role": "user", "content": "anyone there"}], "stream": True}
        with pytest.raises(RuntimeError, match="never spoke"):
            async for _ in proxy.forward_chat_completion_stream(body):
                pass

        rows = _all_messages(repo)
        # Critical: 2 rows, not 0 — the failure must be observable
        assert len(rows) == 2
        asst = next(r for r in rows if r["role"] == "assistant")
        assert asst["capture_outcome"] == "stream_aborted"
        assert asst["content"] == ""
        assert "never spoke" in (asst["error_message"] or "")

    @pytest.mark.asyncio
    async def test_successful_stream_outcome_is_ok_not_aborted(self, stream_proxy_setup):
        """Belt + suspenders: a clean stream lands as outcome=ok. Without this
        test, regressions that mark every stream as 'aborted' would still
        pass the failure-mode tests above."""
        proxy, repo, router, _vec, _ = stream_proxy_setup
        router.queue_chunks([
            'data: {"choices":[{"delta":{"content":"clean "},"index":0}]}',
            'data: {"choices":[{"delta":{"content":"finish"},"index":0}]}',
            'data: [DONE]',
        ])

        body = {"model": "test", "messages": [{"role": "user", "content": "ok"}], "stream": True}
        async for _ in proxy.forward_chat_completion_stream(body):
            pass

        rows = _all_messages(repo)
        asst = next(r for r in rows if r["role"] == "assistant")
        assert asst["capture_outcome"] == "ok"
        assert asst["content"] == "clean finish"
        assert asst["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# TestChatCompletionsMalformedInput — non-streaming + streaming
# ---------------------------------------------------------------------------


class TestChatCompletionsMalformedInput:
    """The proxy itself is permissive about request shape (it just forwards
    body to the backend) — these tests pin down the OBSERVED behavior so a
    future "tighten validation" change is a deliberate, breaking edit, not
    a silent regression of two paths in opposite directions.
    """

    @pytest.mark.asyncio
    async def test_empty_messages_array_still_forwards(self, proxy_setup):
        """An empty ``messages`` array is a real (degenerate) request shape —
        OpenAI rejects it with a 400. BeigeBox today forwards it; assert the
        forward happens AND nothing crashes the capture pipeline."""
        proxy, repo, router, _vec, _ = proxy_setup
        router.queue(_ok_response("noop"))

        body = {"model": "test", "messages": []}
        result = await proxy.forward_chat_completion(body)

        # Backend was called, response surfaced
        assert result["choices"][0]["message"]["content"] == "noop"
        # No user row was created (no user message), but assistant row exists
        rows = _all_messages(repo)
        # The capture envelope still creates a request row with empty content
        # — assertion: at least the assistant row landed
        roles = [r["role"] for r in rows]
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_missing_model_field_uses_default(self, proxy_setup):
        """``model`` is optional in BeigeBox — it falls back to the configured
        default (cfg.backend.default_model). Without this test, removing the
        fallback would silently break every client that omits the field."""
        proxy, repo, router, _vec, _ = proxy_setup
        router.queue(_ok_response("ack"))

        body = {"messages": [{"role": "user", "content": "hi"}]}
        result = await proxy.forward_chat_completion(body)
        assert result["choices"][0]["message"]["content"] == "ack"

        # Default model from cfg lands on the row
        rows = _all_messages(repo)
        asst = next(r for r in rows if r["role"] == "assistant")
        assert asst["model"] == "test"  # matches the patched cfg default

    @pytest.mark.asyncio
    async def test_non_dict_message_content_does_not_crash_capture(self, proxy_setup):
        """OpenAI v2 lets ``content`` be a list of parts (text+image). The
        capture pipeline must not assume str. Regression guard for a class of
        bugs where the row insert raises and takes the whole request down."""
        proxy, repo, router, _vec, _ = proxy_setup
        router.queue(_ok_response("ok"))

        body = {
            "model": "test",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            ],
        }
        # Should NOT raise
        result = await proxy.forward_chat_completion(body)
        assert result["choices"][0]["message"]["content"] == "ok"
        # And rows landed
        rows = _all_messages(repo)
        assert len(rows) >= 1
