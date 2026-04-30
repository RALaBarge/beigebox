"""
Tests for beigebox/cache.py — ToolResultCache.

(EmbeddingCache + SemanticCache were removed in the v3-thin-proxy era; their
tests went with them.)
"""

import time
import pytest

from beigebox.cache import ToolResultCache


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

    def test_stats_reports(self):
        c = ToolResultCache()
        c.put("t", "q", "v")
        c.get("t", "q")  # hit
        c.get("t", "missing")  # miss
        stats = c.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
