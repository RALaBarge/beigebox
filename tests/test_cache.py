"""
Tests for beigebox/cache.py — EmbeddingCache and ToolResultCache.
"""

import time
import numpy as np
import pytest
from unittest.mock import patch

from beigebox.cache import EmbeddingCache, ToolResultCache


# ── EmbeddingCache ────────────────────────────────────────────────────────────

class TestEmbeddingCache:
    def test_miss_returns_none(self):
        c = EmbeddingCache()
        assert c.get("never stored") is None

    def test_hit_returns_vector(self):
        c = EmbeddingCache()
        vec = np.array([0.1, 0.2, 0.3])
        c.put("hello", vec)
        result = c.get("hello")
        assert result is not None
        np.testing.assert_array_equal(result, vec)

    def test_different_keys_independent(self):
        c = EmbeddingCache()
        c.put("a", np.array([1.0]))
        c.put("b", np.array([2.0]))
        assert c.get("a")[0] == pytest.approx(1.0)
        assert c.get("b")[0] == pytest.approx(2.0)

    def test_size_tracks_entries(self):
        c = EmbeddingCache()
        assert c.size() == 0
        c.put("x", np.array([0.0]))
        assert c.size() == 1
        c.put("y", np.array([0.0]))
        assert c.size() == 2

    def test_expired_entry_returns_none(self):
        c = EmbeddingCache(ttl=0.01)
        c.put("tmp", np.array([1.0]))
        time.sleep(0.05)
        assert c.get("tmp") is None

    def test_max_size_evicts_oldest(self):
        c = EmbeddingCache(max_size=2)
        c.put("first",  np.array([1.0]))
        time.sleep(0.001)  # ensure different timestamps
        c.put("second", np.array([2.0]))
        time.sleep(0.001)
        c.put("third",  np.array([3.0]))  # should evict "first"
        assert c.size() == 2
        # "first" should be gone
        assert c.get("first") is None
        # "second" and "third" should survive
        assert c.get("second") is not None
        assert c.get("third") is not None

    def test_overwrite_existing_key(self):
        c = EmbeddingCache()
        c.put("k", np.array([1.0]))
        c.put("k", np.array([9.0]))
        assert c.get("k")[0] == pytest.approx(9.0)
        assert c.size() == 1


# ── ToolResultCache ───────────────────────────────────────────────────────────

class TestToolResultCache:
    def test_miss_returns_none(self):
        c = ToolResultCache()
        assert c.get("web_search", "AI news") is None

    def test_hit_returns_result(self):
        c = ToolResultCache()
        c.put("calculator", "2+2", "4")
        assert c.get("calculator", "2+2") == "4"

    def test_different_tools_independent(self):
        c = ToolResultCache()
        c.put("tool_a", "q", "result_a")
        c.put("tool_b", "q", "result_b")
        assert c.get("tool_a", "q") == "result_a"
        assert c.get("tool_b", "q") == "result_b"

    def test_different_queries_independent(self):
        c = ToolResultCache()
        c.put("search", "cats", "meow")
        c.put("search", "dogs", "woof")
        assert c.get("search", "cats") == "meow"
        assert c.get("search", "dogs") == "woof"

    def test_expired_returns_none(self):
        c = ToolResultCache(ttl=0.01)
        c.put("t", "q", "v")
        time.sleep(0.05)
        assert c.get("t", "q") is None

    def test_custom_ttl(self):
        c = ToolResultCache(ttl=3600)
        c.put("t", "q", "val")
        assert c.get("t", "q") == "val"

    def test_empty_result_cacheable(self):
        c = ToolResultCache()
        c.put("tool", "query", "")
        assert c.get("tool", "query") == ""

    def test_key_is_deterministic(self):
        """Same tool+query always hits the same cache slot."""
        c = ToolResultCache()
        c.put("web_search", "python", "result1")
        assert c.get("web_search", "python") == "result1"
        # Overwrite
        c.put("web_search", "python", "result2")
        assert c.get("web_search", "python") == "result2"
