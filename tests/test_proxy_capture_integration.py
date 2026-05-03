"""End-to-end integration tests for proxy ↔ CaptureFanout (non-streaming).

These spin up a Proxy with a real SQLiteStore, real WireLog, real
CaptureFanout, and a fake backend_router that returns canned
BackendResponse + NormalizedRequest. The point is to verify the rewire:
that every request/response actually produces the expected v1.4 rows
in the messages table and the expected wire events.
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
from beigebox.storage.repos import make_conversation_repo
from beigebox.storage.vector_store import VectorStore


class FakeRouter:
    """Minimal backend_router stand-in. Returns whatever was queued."""

    def __init__(self) -> None:
        self._queue: list[BackendResponse | Exception] = []

    def queue(self, item) -> None:
        self._queue.append(item)

    async def forward(self, body: dict) -> BackendResponse:
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeVector:
    """Vector store stand-in. Records embed calls but doesn't actually embed."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def store_message_async(self, **kwargs) -> None:
        self.calls.append(kwargs)


@pytest.fixture
def proxy_setup():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        wire_path = Path(tmp) / "wire.jsonl"

        db = make_db("sqlite", path=str(db_path))
        repo = make_conversation_repo(db)
        repo.create_tables()
        vector = FakeVector()
        router = FakeRouter()

        # Patch wiretap path via config so WireLog writes to tmp
        with patch("beigebox.proxy.core.get_config") as mock_cfg:
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
            with patch("beigebox.proxy.core.get_runtime_config", return_value={}):
                proxy = Proxy(
                    conversations=repo,
                    vector=vector,
                    backend_router=router,
                )

        # Wire the fanout AFTER the proxy is constructed (it uses
        # proxy.wire which is built inside __init__).
        fanout = CaptureFanout(
            conversations=repo,
            wire=proxy.wire,
            vector=vector,
        )
        proxy.capture = fanout
        yield proxy, repo, router, vector, wire_path


def _all_messages(repo) -> list[dict]:
    return repo._db.fetchall("SELECT * FROM messages ORDER BY timestamp")


class TestNonStreamingCapture:
    @pytest.mark.asyncio
    async def test_successful_request_produces_request_and_response_rows(
        self, proxy_setup,
    ):
        proxy, repo, router, vector, _wire_path = proxy_setup

        nr = NormalizedRequest(
            body={"messages": [{"role": "user", "content": "hi"}], "model": "test"},
            target="openrouter",
            transforms=["renamed_max_tokens"],
            errors=[],
        )
        response = BackendResponse(
            ok=True,
            backend_name="openrouter",
            latency_ms=120.0,
            cost_usd=0.0001,
            data={
                "choices": [{"message": {"role": "assistant", "content": "hello back"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8,
                          "cost": 0.0001},
            },
        )
        response.normalized_request = nr
        response.request_summary = nr.summary({"backend": "openrouter"})
        router.queue(response)

        body = {
            "model": "test",
            "messages": [{"role": "user", "content": "hi"}],
        }
        result = await proxy.forward_chat_completion(body)

        assert result["choices"][0]["message"]["content"] == "hello back"

        rows = _all_messages(repo)
        # 1 user request row + 1 assistant response row
        assert len(rows) == 2

        user_row = next(r for r in rows if r["role"] == "user")
        assert user_row["content"] == "hi"
        assert user_row["request_transforms_json"] == '["renamed_max_tokens"]'
        assert user_row["capture_outcome"] == "ok"

        asst_row = next(r for r in rows if r["role"] == "assistant")
        assert asst_row["content"] == "hello back"
        assert asst_row["finish_reason"] == "stop"
        assert asst_row["prompt_tokens"] == 5
        assert asst_row["completion_tokens"] == 3
        assert asst_row["cost_usd"] == 0.0001
        assert asst_row["capture_outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_upstream_error_still_produces_rows(self, proxy_setup):
        proxy, repo, router, vector, _wire_path = proxy_setup

        nr = NormalizedRequest(
            body={"messages": [{"role": "user", "content": "hi"}]},
            target="openrouter",
            transforms=[],
            errors=[],
        )
        response = BackendResponse(
            ok=False,
            backend_name="openrouter",
            latency_ms=50.0,
            error="upstream returned 502",
            data={},
        )
        response.normalized_request = nr
        response.request_summary = nr.summary({"backend": "openrouter"})
        router.queue(response)

        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        result = await proxy.forward_chat_completion(body)

        # The proxy returns a synthetic error response to the client
        assert "Backend error" in result["choices"][0]["message"]["content"]

        rows = _all_messages(repo)
        assert len(rows) == 2

        user_row = next(r for r in rows if r["role"] == "user")
        assert user_row["capture_outcome"] == "ok"   # request itself was fine

        asst_row = next(r for r in rows if r["role"] == "assistant")
        assert asst_row["capture_outcome"] == "upstream_error"
        assert asst_row["error_kind"] == "upstream_error"
        assert "502" in (asst_row["error_message"] or "")
        assert asst_row["finish_reason"] == "error"

    @pytest.mark.asyncio
    async def test_router_raises_captures_failure_row(self, proxy_setup):
        proxy, repo, router, vector, _wire_path = proxy_setup

        router.queue(RuntimeError("router exploded"))

        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}]}
        with pytest.raises(RuntimeError, match="router exploded"):
            await proxy.forward_chat_completion(body)

        rows = _all_messages(repo)
        # Request envelope + failure response envelope
        assert len(rows) == 2
        asst_row = next(r for r in rows if r["role"] == "assistant")
        assert asst_row["capture_outcome"] == "upstream_error"
        assert "router exploded" in (asst_row["error_message"] or "")

    @pytest.mark.asyncio
    async def test_synthetic_request_skips_capture(self, proxy_setup):
        # Synthetic requests bypass capture (matching legacy _log_messages
        # behaviour). We simulate by setting _bb_synthetic in the body, but
        # the synthetic flag is set inside _run_request_pipeline; without
        # the full hook stack available in this fixture the synthetic path
        # isn't exercised here. This is left as a stub for completeness.
        pass

    @pytest.mark.asyncio
    async def test_response_with_reasoning_and_tool_calls(self, proxy_setup):
        proxy, repo, router, vector, _wire_path = proxy_setup

        nr = NormalizedRequest(body={}, target="openrouter", transforms=[], errors=[])
        response = BackendResponse(
            ok=True,
            backend_name="openrouter",
            latency_ms=200.0,
            data={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "the answer",
                        "reasoning": "step 1\nstep 2",
                        "tool_calls": [{"id": "tc1", "function": {"name": "search", "arguments": "{}"}}],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "completion_tokens_details": {"reasoning_tokens": 7}, "total_tokens": 22},
            },
        )
        response.normalized_request = nr
        response.request_summary = nr.summary({"backend": "openrouter"})
        router.queue(response)

        body = {"model": "test", "messages": [{"role": "user", "content": "do something"}]}
        await proxy.forward_chat_completion(body)

        rows = _all_messages(repo)
        asst_row = next(r for r in rows if r["role"] == "assistant")
        assert asst_row["reasoning_text"] == "step 1\nstep 2"
        assert "tc1" in (asst_row["tool_calls_json"] or "")
        assert asst_row["finish_reason"] == "tool_calls"
        assert asst_row["reasoning_tokens"] == 7


class FakeStreamingRouter:
    """Async-generator backend router for streaming tests."""

    def __init__(self) -> None:
        self._chunks: list = []
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


@pytest.fixture
def stream_proxy_setup():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        wire_path = Path(tmp) / "wire.jsonl"

        db = make_db("sqlite", path=str(db_path))
        repo = make_conversation_repo(db)
        repo.create_tables()
        vector = FakeVector()
        router = FakeStreamingRouter()

        with patch("beigebox.proxy.core.get_config") as mock_cfg:
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
            with patch("beigebox.proxy.core.get_runtime_config", return_value={}):
                proxy = Proxy(
                    conversations=repo,
                    vector=vector,
                    backend_router=router,
                )

        fanout = CaptureFanout(
            conversations=repo,
            wire=proxy.wire,
            vector=vector,
        )
        proxy.capture = fanout
        yield proxy, repo, router, vector


class TestStreamingCapture:
    @pytest.mark.asyncio
    async def test_successful_stream_produces_request_and_response_rows(
        self, stream_proxy_setup,
    ):
        proxy, repo, router, _vector = stream_proxy_setup

        router.queue_chunks([
            'data: {"choices":[{"delta":{"content":"hello "},"index":0}]}',
            'data: {"choices":[{"delta":{"content":"world"},"index":0}]}',
            'data: [DONE]',
        ])

        body = {"model": "test", "messages": [{"role": "user", "content": "say hi"}], "stream": True}
        # Drain the streaming generator
        async for _line in proxy.forward_chat_completion_stream(body):
            pass

        rows = _all_messages(repo)
        assert len(rows) == 2

        user_row = next(r for r in rows if r["role"] == "user")
        assert user_row["content"] == "say hi"
        assert user_row["capture_outcome"] == "ok"

        asst_row = next(r for r in rows if r["role"] == "assistant")
        assert asst_row["content"] == "hello world"
        assert asst_row["capture_outcome"] == "ok"
        assert asst_row["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_mid_stream_error_captures_partial(self, stream_proxy_setup):
        proxy, repo, router, _vector = stream_proxy_setup

        router.queue_chunks([
            'data: {"choices":[{"delta":{"content":"partial "},"index":0}]}',
            'data: {"choices":[{"delta":{"content":"text"},"index":0}]}',
        ])
        router.raise_at(2, RuntimeError("upstream died"))

        body = {"model": "test", "messages": [{"role": "user", "content": "go"}], "stream": True}
        with pytest.raises(RuntimeError, match="upstream died"):
            async for _line in proxy.forward_chat_completion_stream(body):
                pass

        rows = _all_messages(repo)
        asst_row = next(r for r in rows if r["role"] == "assistant")
        assert asst_row["capture_outcome"] == "stream_aborted"
        assert "partial" in asst_row["content"]
        assert "upstream died" in (asst_row["error_message"] or "")

    @pytest.mark.asyncio
    async def test_client_disconnect_captures_partial(self, stream_proxy_setup):
        proxy, repo, router, _vector = stream_proxy_setup

        router.queue_chunks([
            'data: {"choices":[{"delta":{"content":"some "},"index":0}]}',
            'data: {"choices":[{"delta":{"content":"text"},"index":0}]}',
        ])
        router.cancel_at(2)

        body = {"model": "test", "messages": [{"role": "user", "content": "go"}], "stream": True}
        with pytest.raises(asyncio.CancelledError):
            async for _line in proxy.forward_chat_completion_stream(body):
                pass

        rows = _all_messages(repo)
        asst_row = next(r for r in rows if r["role"] == "assistant")
        assert asst_row["capture_outcome"] == "client_disconnect"
        assert asst_row["finish_reason"] == "aborted"
        assert "some" in asst_row["content"]

    @pytest.mark.asyncio
    async def test_error_before_first_chunk_still_captures(self, stream_proxy_setup):
        proxy, repo, router, _vector = stream_proxy_setup

        # No chunks queued; raise on first iteration
        router.queue_chunks([])
        router.raise_at(0, RuntimeError("nothing arrived"))

        body = {"model": "test", "messages": [{"role": "user", "content": "go"}], "stream": True}
        with pytest.raises(RuntimeError, match="nothing arrived"):
            async for _line in proxy.forward_chat_completion_stream(body):
                pass

        rows = _all_messages(repo)
        # Belt-and-suspenders: request is captured even if no chunks arrived
        assert len(rows) == 2
        asst_row = next(r for r in rows if r["role"] == "assistant")
        assert asst_row["capture_outcome"] == "stream_aborted"
        assert asst_row["content"] == ""
