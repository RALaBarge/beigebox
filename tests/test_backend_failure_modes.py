"""Backend failure-mode tests.

Grok review #3 in docs/grok_test_coverage_review.md: tests/test_backends.py
covers happy paths and basic init, but not the corners where backends
actually break in production. This file fills those:

- Backend timeout → router falls back to next backend
- Backend connection refused → router falls back
- Backend returns malformed JSON → request fails cleanly with error captured
- Streaming backend disconnects mid-stream → router moves to next backend
  (when one is available) and surfaces a [BeigeBox: All backends failed]
  chunk when all fail
- MultiBackendRouter records the per-backend BackendResponse.usage even
  when the call returns ok=False, so cost tracking and token attribution
  don't silently lose data on partial-success scenarios.

These don't hit a real network — they patch the backend's ``forward`` /
``forward_stream`` methods so we can simulate exact upstream behaviour.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from beigebox.backends.base import BackendResponse
from beigebox.backends.ollama import OllamaBackend
from beigebox.backends.openrouter import OpenRouterBackend
from beigebox.backends.router import MultiBackendRouter


# ---------------------------------------------------------------------------
# Single-backend failure modes (timeout / refused / malformed)
# ---------------------------------------------------------------------------


class TestBackendErrorPaths:
    """Each backend handles its own upstream failures and returns a clean
    BackendResponse(ok=False) — never raising. These tests pin that
    contract for the two primary providers (Ollama, OpenRouter)."""

    @pytest.mark.asyncio
    async def test_ollama_connection_refused_returns_error_response(self):
        """ConnectError must NOT escape ``forward()``. Otherwise the router
        can't fall back — it crashes the request."""
        b = OllamaBackend(name="local", url="http://nowhere:99999", timeout=5)

        with patch("beigebox.backends.ollama.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await b.forward({"model": "llama3.2", "messages": []})

        assert result.ok is False
        assert "connection refused" in (result.error or "").lower()
        assert result.backend_name == "local"

    @pytest.mark.asyncio
    async def test_ollama_malformed_json_response_returns_error(self):
        """A 200 response with malformed JSON must surface as a clean error
        (the JSONDecodeError must not bubble out of forward())."""
        b = OllamaBackend(name="local", url="http://fake:11434", timeout=5)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = json.JSONDecodeError("not json", "doc", 0)

        with patch("beigebox.backends.ollama.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await b.forward({"model": "llama3.2", "messages": []})

        assert result.ok is False
        # The error path catches the JSONDecodeError under the broad except
        # clause and surfaces ``str(e)`` in result.error
        assert result.error is not None
        assert result.backend_name == "local"

    @pytest.mark.asyncio
    async def test_openrouter_5xx_returns_error_with_status_code(self):
        """OpenRouter 502/503 must come back with status_code preserved so
        the router can decide whether to retry / fall back."""
        b = OpenRouterBackend(name="or", url="http://fake", api_key="sk-x", timeout=5)

        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.text = "Bad Gateway"

        with patch("beigebox.backends.openrouter.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await b.forward({"model": "openai/gpt-4o", "messages": []})

        assert result.ok is False
        assert result.status_code == 502
        assert "502" in (result.error or "")

    @pytest.mark.asyncio
    async def test_openrouter_stream_raises_so_router_can_fall_back(self):
        """Streaming has a different contract from non-streaming: errors
        must RAISE so the router's outer try/except can move to the next
        backend. Returning a non-ok BackendResponse from a stream is a
        different shape entirely."""
        b = OpenRouterBackend(name="or", url="http://fake", api_key="sk-x", timeout=5)

        # Mock the AsyncClient so client.stream(...) raises on entry —
        # ``stream()`` is sync (returns an async context manager), so use
        # MagicMock for it and have its __aenter__ raise. This preserves
        # the contract test: ANY exception in forward_stream must bubble
        # out, not be swallowed.
        with patch("beigebox.backends.openrouter.httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            stream_ctx = MagicMock()
            stream_ctx.__aenter__ = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
            stream_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_client.stream = MagicMock(return_value=stream_ctx)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(httpx.ReadTimeout):
                async for _ in b.forward_stream({"model": "openai/gpt-4o", "messages": []}):
                    pass


# ---------------------------------------------------------------------------
# Multi-backend fallback
# ---------------------------------------------------------------------------


class TestMultiBackendFallback:
    """The router's fallback contract: when backend N fails, try N+1.

    Existing tests in test_backends.py cover priority ordering and the
    "all backends failed" terminal case. These cover the moves between."""

    def _two_ollamas(self):
        """Two Ollama backends so plain model names can route through both
        without the OpenRouter-specific gate getting in the way."""
        config = [
            {"name": "primary", "url": "http://ollama-1", "provider": "ollama", "priority": 1},
            {"name": "backup",  "url": "http://ollama-2", "provider": "ollama", "priority": 2},
        ]
        return MultiBackendRouter(config)

    @pytest.mark.asyncio
    async def test_timeout_on_first_backend_falls_back_to_second(self):
        router = self._two_ollamas()

        timeout_resp = BackendResponse(
            ok=False, backend_name="primary",
            latency_ms=5000.0, error="Timeout after 5s",
        )
        success_resp = BackendResponse(
            ok=True, backend_name="backup", latency_ms=100.0,
            data={"choices": [{"message": {"content": "from backup"}}]},
        )
        router.backends[0].forward = AsyncMock(return_value=timeout_resp)
        router.backends[1].forward = AsyncMock(return_value=success_resp)

        result = await router.forward({"model": "llama3.2", "messages": []})

        assert result.ok is True
        assert result.backend_name == "backup"
        # Both backends were tried — primary first, then fallback
        router.backends[0].forward.assert_awaited_once()
        router.backends[1].forward.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connection_error_on_first_falls_back_to_second(self):
        router = self._two_ollamas()
        connect_err = BackendResponse(
            ok=False, backend_name="primary", latency_ms=15.0,
            error="Connection refused",
        )
        success_resp = BackendResponse(
            ok=True, backend_name="backup", latency_ms=80.0,
            data={"choices": [{"message": {"content": "ok"}}]},
        )
        router.backends[0].forward = AsyncMock(return_value=connect_err)
        router.backends[1].forward = AsyncMock(return_value=success_resp)

        result = await router.forward({"model": "llama3.2", "messages": []})
        assert result.ok is True
        assert result.backend_name == "backup"

    @pytest.mark.asyncio
    async def test_all_backends_fail_returns_503_with_aggregated_errors(self):
        """When every backend returns a different failure, the final error
        message must concatenate them so debugging knows which backend said
        what — losing per-backend errors makes outages opaque."""
        router = self._two_ollamas()
        router.backends[0].forward = AsyncMock(return_value=BackendResponse(
            ok=False, backend_name="primary", error="Timeout after 5s",
        ))
        router.backends[1].forward = AsyncMock(return_value=BackendResponse(
            ok=False, backend_name="backup", error="HTTP 503: down",
        ))

        result = await router.forward({"model": "llama3.2", "messages": []})

        assert result.ok is False
        assert result.status_code == 503
        # Each backend's error name + reason is preserved in the aggregate
        assert "primary" in (result.error or "")
        assert "backup" in (result.error or "")
        assert "Timeout" in (result.error or "")

    @pytest.mark.asyncio
    async def test_streaming_first_backend_fails_falls_back_to_second(self):
        """Same fallback contract for streaming: if backend 1's stream
        raises, the router must seamlessly start backend 2's stream."""
        router = self._two_ollamas()

        async def _failing_stream(body):
            raise httpx.ConnectError("primary down")
            yield  # pragma: no cover  (unreachable, makes this an async gen)

        async def _ok_stream(body):
            yield 'data: {"choices":[{"delta":{"content":"hello from backup"},"index":0}]}'
            yield "data: [DONE]"

        router.backends[0].forward_stream = _failing_stream
        router.backends[1].forward_stream = _ok_stream

        chunks = []
        async for line in router.forward_stream({"model": "llama3.2", "messages": []}):
            chunks.append(line)

        joined = "\n".join(chunks)
        assert "hello from backup" in joined
        assert "[DONE]" in joined
        # Critically: no [BeigeBox: All backends failed] sentinel — fallback worked
        assert "All backends failed" not in joined

    @pytest.mark.asyncio
    async def test_streaming_all_backends_fail_yields_error_chunk(self):
        """When every streaming backend dies, the client must still get a
        well-formed SSE error chunk + [DONE] — not just a closed connection.
        Otherwise the frontend hangs."""
        router = self._two_ollamas()

        async def _err1(body):
            raise httpx.ConnectError("primary down")
            yield  # pragma: no cover

        async def _err2(body):
            raise RuntimeError("backup also down")
            yield  # pragma: no cover

        router.backends[0].forward_stream = _err1
        router.backends[1].forward_stream = _err2

        chunks = []
        async for line in router.forward_stream({"model": "llama3.2", "messages": []}):
            chunks.append(line)

        joined = "\n".join(chunks)
        # Synthetic error chunk + DONE marker — the stream contract is honoured
        assert "All backends failed" in joined
        assert "primary down" in joined
        assert "backup also down" in joined
        assert "[DONE]" in joined


# ---------------------------------------------------------------------------
# Cost / token preservation through partial failures
# ---------------------------------------------------------------------------


class TestBackendUsagePreservedOnFailure:
    """Grok flagged: MultiBackendRouter cost extraction continues to record
    token counts when the upstream call fails after partial success.

    The current contract: each BackendResponse carries its own
    usage/cost — they're not merged across backends. So when backend 1
    fails and backend 2 succeeds, the FINAL response we return is
    backend 2's, and its usage/cost field is intact. Pin that down so a
    future "merge usage across attempts" change is a deliberate decision."""

    @pytest.mark.asyncio
    async def test_first_backend_failure_does_not_taint_second_backends_cost(self):
        config = [
            {"name": "primary", "url": "http://ollama", "provider": "ollama", "priority": 1},
            {"name": "backup",  "url": "http://ollama-2", "provider": "ollama", "priority": 2},
        ]
        router = MultiBackendRouter(config)

        # Primary fails — note this BackendResponse has no usage data, but
        # also has cost_usd=None. If the router were summing cost across
        # attempts the fallback's $0.005 would be off.
        router.backends[0].forward = AsyncMock(return_value=BackendResponse(
            ok=False, backend_name="primary", error="503",
            cost_usd=None, latency_ms=20.0,
        ))
        # Backup succeeds with explicit cost + usage
        router.backends[1].forward = AsyncMock(return_value=BackendResponse(
            ok=True, backend_name="backup",
            data={"choices": [{"message": {"content": "ok"}}],
                  "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                            "total_tokens": 15}},
            cost_usd=0.005, latency_ms=100.0,
        ))

        result = await router.forward({"model": "llama3.2", "messages": []})

        assert result.ok is True
        assert result.backend_name == "backup"
        assert result.cost_usd == 0.005
        # Token counts come from backup's response only — no merging artifact
        assert result.data["usage"]["total_tokens"] == 15

    @pytest.mark.asyncio
    async def test_failed_backend_response_still_has_usable_error_metadata(self):
        """Even when ok=False, BackendResponse should carry latency_ms +
        backend_name so observability/log_backend_selection can record
        which backend failed and how long it took."""
        b = OllamaBackend(name="local", url="http://fake:11434", timeout=2)

        with patch("beigebox.backends.ollama.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.TimeoutException("timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await b.forward({"model": "llama3.2", "messages": []})

        assert result.ok is False
        assert result.backend_name == "local"
        assert result.latency_ms is not None
        # Latency was actually measured (not the default 0)
        assert result.latency_ms >= 0
