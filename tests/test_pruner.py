"""Tests for beigebox.agents.pruner.ContextPruner."""
import pytest
from unittest.mock import patch, MagicMock
from beigebox.agents.pruner import ContextPruner


# ── from_config disabled ──────────────────────────────────────────────────────

def test_from_config_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        "beigebox.agents.pruner.get_config",
        lambda: {"operator": {"context_pruning": {"enabled": False}}},
    )
    monkeypatch.setattr("beigebox.agents.pruner.get_runtime_config", lambda: {})
    pruner = ContextPruner.from_config()
    assert not pruner.enabled


def test_from_config_enabled(monkeypatch):
    monkeypatch.setattr(
        "beigebox.agents.pruner.get_config",
        lambda: {
            "operator": {"context_pruning": {"enabled": True, "model": "qwen:0.5b", "timeout": 5}},
            "backend": {"url": "http://localhost:11434", "default_model": "qwen:0.5b"},
        },
    )
    monkeypatch.setattr("beigebox.agents.pruner.get_runtime_config", lambda: {})
    pruner = ContextPruner.from_config()
    assert pruner.enabled
    assert pruner._model == "qwen:0.5b"
    assert pruner._timeout == 5


# ── prune returns original when disabled ──────────────────────────────────────

def test_prune_noop_when_disabled():
    pruner = ContextPruner.__new__(ContextPruner)
    pruner._enabled = False
    pruner._model = ""
    pruner._backend_url = ""
    pruner._timeout = 8
    ctx = "some long context block here"
    assert pruner.prune(ctx, "step 1") == ctx


def test_prune_noop_empty_input():
    pruner = ContextPruner("model", "http://localhost:11434")
    assert pruner.prune("", "step 1") == ""
    assert pruner.prune("   ", "step 1") == "   "


# ── prune returns original on HTTP error ─────────────────────────────────────

def test_prune_returns_original_on_error():
    pruner = ContextPruner("model", "http://localhost:11434", timeout=1)
    ctx = "A" * 500
    with patch("beigebox.agents.pruner.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.side_effect = Exception("connection refused")
        result = pruner.prune(ctx, "build API")
    assert result == ctx


def test_prune_returns_original_if_longer():
    """If LLM returns something longer than original, keep original."""
    pruner = ContextPruner("model", "http://localhost:11434")
    ctx = "short"
    expanded = "this is much much much much longer than the original"
    with patch("beigebox.agents.pruner.httpx.Client") as MockClient:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": expanded}}]
        }
        MockClient.return_value.__enter__.return_value.post.return_value = mock_resp
        result = pruner.prune(ctx, "step 1")
    assert result == ctx


def test_prune_returns_compressed_when_shorter():
    pruner = ContextPruner("model", "http://localhost:11434")
    ctx = "A very long context " * 50
    compressed = "Short compressed context."
    with patch("beigebox.agents.pruner.httpx.Client") as MockClient:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": compressed}}]
        }
        MockClient.return_value.__enter__.return_value.post.return_value = mock_resp
        result = pruner.prune(ctx, "implement step 2")
    assert result == compressed


def test_prune_returns_original_on_empty_llm_response():
    pruner = ContextPruner("model", "http://localhost:11434")
    ctx = "some context"
    with patch("beigebox.agents.pruner.httpx.Client") as MockClient:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": ""}}]
        }
        MockClient.return_value.__enter__.return_value.post.return_value = mock_resp
        result = pruner.prune(ctx, "step")
    assert result == ctx
