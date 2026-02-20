"""
Tests for multi-backend router.
Run with: pytest tests/test_backends.py
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from beigebox.backends.base import BaseBackend, BackendResponse
from beigebox.backends.ollama import OllamaBackend
from beigebox.backends.openrouter import OpenRouterBackend
from beigebox.backends.router import MultiBackendRouter


# ---------------------------------------------------------------------------
# BackendResponse
# ---------------------------------------------------------------------------

def test_backend_response_ok():
    """BackendResponse reports ok/error correctly."""
    ok = BackendResponse(ok=True, data={"choices": [{"message": {"content": "hi"}}]})
    assert ok.ok
    assert ok.content == "hi"

    err = BackendResponse(ok=False, error="timeout")
    assert not err.ok
    assert err.content == ""


def test_backend_response_cost():
    """BackendResponse carries cost_usd."""
    resp = BackendResponse(ok=True, cost_usd=0.0015)
    assert resp.cost_usd == 0.0015

    local = BackendResponse(ok=True, cost_usd=None)
    assert local.cost_usd is None


# ---------------------------------------------------------------------------
# OllamaBackend
# ---------------------------------------------------------------------------

def test_ollama_backend_init():
    """OllamaBackend initializes with correct attributes."""
    b = OllamaBackend(name="local", url="http://localhost:11434", timeout=60, priority=1)
    assert b.name == "local"
    assert b.url == "http://localhost:11434"
    assert b.timeout == 60
    assert b.priority == 1


@pytest.mark.asyncio
async def test_ollama_forward_success():
    """OllamaBackend forwards and returns data on success."""
    b = OllamaBackend(name="test", url="http://fake:11434")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "hello"}}]}

    with patch("beigebox.backends.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await b.forward({"model": "llama3.2", "messages": []})
        assert result.ok
        assert result.content == "hello"
        assert result.cost_usd is None  # Ollama is always free
        assert result.backend_name == "test"


@pytest.mark.asyncio
async def test_ollama_forward_timeout():
    """OllamaBackend returns error on timeout."""
    import httpx
    b = OllamaBackend(name="test", url="http://fake:11434", timeout=1)

    with patch("beigebox.backends.ollama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await b.forward({"model": "llama3.2", "messages": []})
        assert not result.ok
        assert "Timeout" in result.error


# ---------------------------------------------------------------------------
# OpenRouterBackend
# ---------------------------------------------------------------------------

def test_openrouter_env_resolution():
    """OpenRouterBackend resolves ${ENV_VAR} references."""
    import os
    os.environ["TEST_API_KEY_12345"] = "sk-test-key"
    b = OpenRouterBackend(name="or", url="http://fake", api_key="${TEST_API_KEY_12345}")
    assert b.api_key == "sk-test-key"
    del os.environ["TEST_API_KEY_12345"]


def test_openrouter_cost_extraction():
    """OpenRouterBackend extracts cost from response data."""
    # Direct cost_usd field
    assert OpenRouterBackend._extract_cost({"cost_usd": 0.005}) == 0.005

    # Usage.cost field
    assert OpenRouterBackend._extract_cost({"usage": {"cost": 0.003}}) == 0.003

    # No cost available
    assert OpenRouterBackend._extract_cost({"choices": []}) is None


@pytest.mark.asyncio
async def test_openrouter_no_api_key():
    """OpenRouterBackend returns error without API key."""
    b = OpenRouterBackend(name="or", url="http://fake", api_key="")
    result = await b.forward({"model": "gpt-4", "messages": []})
    assert not result.ok
    assert "API key" in result.error


# ---------------------------------------------------------------------------
# MultiBackendRouter
# ---------------------------------------------------------------------------

def test_router_init_from_config():
    """Router creates backends from config and sorts by priority."""
    config = [
        {"name": "openrouter", "url": "http://or", "provider": "openrouter", "priority": 2, "api_key": "sk-x"},
        {"name": "local", "url": "http://ollama", "provider": "ollama", "priority": 1},
    ]
    router = MultiBackendRouter(config)
    assert len(router.backends) == 2
    assert router.backends[0].name == "local"  # Priority 1 first
    assert router.backends[1].name == "openrouter"


def test_router_skips_unknown_provider():
    """Router skips backends with unknown provider."""
    config = [
        {"name": "unknown", "url": "http://x", "provider": "foobar"},
        {"name": "local", "url": "http://ollama", "provider": "ollama", "priority": 1},
    ]
    router = MultiBackendRouter(config)
    assert len(router.backends) == 1
    assert router.backends[0].name == "local"


def test_router_skips_missing_url():
    """Router skips backends with no URL."""
    config = [
        {"name": "broken", "provider": "ollama"},
    ]
    router = MultiBackendRouter(config)
    assert len(router.backends) == 0


@pytest.mark.asyncio
async def test_router_forward_tries_priority_order():
    """Router tries backends in priority order and returns first success."""
    config = [
        {"name": "local", "url": "http://ollama", "provider": "ollama", "priority": 1},
        {"name": "or", "url": "http://or", "provider": "openrouter", "priority": 2, "api_key": "sk-x"},
    ]
    router = MultiBackendRouter(config)

    # Mock first backend to succeed
    success_resp = BackendResponse(ok=True, data={"choices": [{"message": {"content": "from local"}}]},
                                   backend_name="local", latency_ms=50)
    router.backends[0].forward = AsyncMock(return_value=success_resp)
    router.backends[1].forward = AsyncMock()  # Should not be called

    result = await router.forward({"model": "llama3.2", "messages": []})
    assert result.ok
    assert result.backend_name == "local"
    router.backends[1].forward.assert_not_called()


@pytest.mark.asyncio
async def test_router_forward_fallback_on_failure():
    """Router falls back to next backend when primary fails."""
    config = [
        {"name": "local", "url": "http://ollama", "provider": "ollama", "priority": 1},
        {"name": "or", "url": "http://or", "provider": "openrouter", "priority": 2, "api_key": "sk-x"},
    ]
    router = MultiBackendRouter(config)

    # First backend fails, second succeeds
    fail_resp = BackendResponse(ok=False, error="timeout", backend_name="local")
    success_resp = BackendResponse(ok=True, data={"choices": [{"message": {"content": "from or"}}]},
                                   backend_name="or", cost_usd=0.001)
    router.backends[0].forward = AsyncMock(return_value=fail_resp)
    router.backends[1].forward = AsyncMock(return_value=success_resp)

    result = await router.forward({"model": "llama3.2", "messages": []})
    assert result.ok
    assert result.backend_name == "or"
    assert result.cost_usd == 0.001


@pytest.mark.asyncio
async def test_router_forward_all_fail():
    """Router returns error when all backends fail."""
    config = [
        {"name": "local", "url": "http://ollama", "provider": "ollama", "priority": 1},
    ]
    router = MultiBackendRouter(config)

    fail_resp = BackendResponse(ok=False, error="down", backend_name="local")
    router.backends[0].forward = AsyncMock(return_value=fail_resp)

    result = await router.forward({"model": "test", "messages": []})
    assert not result.ok
    assert result.status_code == 503
    assert "All backends failed" in result.error


def test_router_get_backend():
    """Router can retrieve a specific backend by name."""
    config = [
        {"name": "local", "url": "http://ollama", "provider": "ollama", "priority": 1},
    ]
    router = MultiBackendRouter(config)
    assert router.get_backend("local") is not None
    assert router.get_backend("nonexistent") is None
