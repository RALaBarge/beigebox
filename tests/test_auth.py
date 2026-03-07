"""
Tests for beigebox/auth.py — MultiKeyAuthRegistry.
"""

import os
import time
import pytest
from unittest.mock import patch

from beigebox.auth import MultiKeyAuthRegistry, KeyMeta


# ── Helpers ───────────────────────────────────────────────────────────────────

def _registry(auth_cfg: dict) -> MultiKeyAuthRegistry:
    """Build a registry, stubbing _resolve_token to avoid keychain calls."""
    with patch("beigebox.auth._resolve_token", return_value=None):
        return MultiKeyAuthRegistry(auth_cfg)


def _registry_with_token(name: str, token: str, extra: dict | None = None) -> MultiKeyAuthRegistry:
    cfg = {"keys": [{**(extra or {}), "name": name}]}
    with patch("beigebox.auth._resolve_token", return_value=token):
        return MultiKeyAuthRegistry(cfg)


# ── is_enabled ────────────────────────────────────────────────────────────────

class TestIsEnabled:
    def test_disabled_when_empty(self):
        r = _registry({})
        assert r.is_enabled() is False

    def test_disabled_when_no_keys_resolved(self):
        r = _registry({"keys": [{"name": "ghost"}]})
        assert r.is_enabled() is False

    def test_enabled_with_legacy_key(self):
        r = _registry({"api_key": "secret"})
        assert r.is_enabled() is True

    def test_enabled_with_named_key(self):
        r = _registry_with_token("mykey", "tok123")
        assert r.is_enabled() is True


# ── validate ──────────────────────────────────────────────────────────────────

class TestValidate:
    def test_valid_legacy_token(self):
        r = _registry({"api_key": "secret"})
        meta = r.validate("secret")
        assert meta is not None
        assert meta.name == "default"

    def test_invalid_token_returns_none(self):
        r = _registry({"api_key": "secret"})
        assert r.validate("wrong") is None

    def test_valid_named_token(self):
        r = _registry_with_token("svc", "tok-abc")
        meta = r.validate("tok-abc")
        assert meta is not None
        assert meta.name == "svc"

    def test_empty_token_not_valid(self):
        r = _registry({"api_key": "secret"})
        assert r.validate("") is None

    def test_legacy_key_is_wildcard(self):
        r = _registry({"api_key": "secret"})
        meta = r.validate("secret")
        assert "*" in meta.allowed_models
        assert "*" in meta.allowed_endpoints


# ── check_model ───────────────────────────────────────────────────────────────

class TestCheckModel:
    def _meta(self, models):
        return KeyMeta(name="t", allowed_models=models)

    def test_wildcard_allows_all(self):
        r = _registry({})
        assert r.check_model(self._meta(["*"]), "any-model") is True

    def test_exact_match(self):
        r = _registry({})
        assert r.check_model(self._meta(["llama3.2"]), "llama3.2") is True

    def test_exact_mismatch(self):
        r = _registry({})
        assert r.check_model(self._meta(["llama3.2"]), "qwen3:8b") is False

    def test_glob_match(self):
        r = _registry({})
        assert r.check_model(self._meta(["qwen3:*"]), "qwen3:8b") is True
        assert r.check_model(self._meta(["qwen3:*"]), "qwen3:14b") is True
        assert r.check_model(self._meta(["qwen3:*"]), "llama3:8b") is False

    def test_multiple_patterns(self):
        r = _registry({})
        meta = self._meta(["llama3.2", "qwen3:*"])
        assert r.check_model(meta, "llama3.2") is True
        assert r.check_model(meta, "qwen3:8b") is True
        assert r.check_model(meta, "mistral:7b") is False


# ── check_endpoint ────────────────────────────────────────────────────────────

class TestCheckEndpoint:
    def _meta(self, endpoints):
        return KeyMeta(name="t", allowed_endpoints=endpoints)

    def test_wildcard_allows_all(self):
        r = _registry({})
        assert r.check_endpoint(self._meta(["*"]), "/v1/chat/completions") is True

    def test_exact_match(self):
        r = _registry({})
        assert r.check_endpoint(self._meta(["/v1/models"]), "/v1/models") is True

    def test_exact_mismatch(self):
        r = _registry({})
        assert r.check_endpoint(self._meta(["/v1/models"]), "/v1/chat/completions") is False

    def test_glob_match(self):
        r = _registry({})
        assert r.check_endpoint(self._meta(["/v1/*"]), "/v1/models") is True
        assert r.check_endpoint(self._meta(["/v1/*"]), "/v1/chat/completions") is True
        assert r.check_endpoint(self._meta(["/v1/*"]), "/api/v1/status") is False


# ── check_rate_limit ──────────────────────────────────────────────────────────

class TestRateLimit:
    def test_unlimited_always_passes(self):
        r = _registry({})
        meta = KeyMeta(name="t", rate_limit_rpm=0)
        for _ in range(200):
            assert r.check_rate_limit(meta) is True

    def test_within_limit_passes(self):
        r = _registry({})
        meta = KeyMeta(name="rl", rate_limit_rpm=5)
        for _ in range(5):
            assert r.check_rate_limit(meta) is True

    def test_exceeds_limit_blocked(self):
        r = _registry({})
        meta = KeyMeta(name="rl2", rate_limit_rpm=3)
        for _ in range(3):
            r.check_rate_limit(meta)
        assert r.check_rate_limit(meta) is False

    def test_window_resets_after_60s(self):
        r = _registry({})
        meta = KeyMeta(name="rl3", rate_limit_rpm=2)
        r.check_rate_limit(meta)
        r.check_rate_limit(meta)
        assert r.check_rate_limit(meta) is False

        # Fake time advancing past 60s
        window = r._rate_windows["rl3"]
        old_ts = window[0]
        window[0] = old_ts - 61.0
        window[1] = old_ts - 61.0

        assert r.check_rate_limit(meta) is True


# ── named key ACL config ──────────────────────────────────────────────────────

class TestNamedKeyACL:
    def test_named_key_has_correct_models(self):
        r = _registry_with_token("ci", "ci-tok", extra={
            "allowed_models": ["llama3.2", "qwen3:*"],
            "allowed_endpoints": ["/v1/chat/completions"],
            "rate_limit_rpm": 60,
        })
        meta = r.validate("ci-tok")
        assert meta.allowed_models == ["llama3.2", "qwen3:*"]
        assert meta.allowed_endpoints == ["/v1/chat/completions"]
        assert meta.rate_limit_rpm == 60

    def test_key_without_token_not_registered(self):
        r = _registry({"keys": [{"name": "ghost"}]})
        assert r.validate("anything") is None
