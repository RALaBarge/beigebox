"""
BeigeBox cache layer — ToolResultCache.

ToolResultCache
    Short-TTL dict for deterministic tool calls (web_search, calculator, etc.).
    Keyed by SHA-256(tool_name + query); TTL default 300 s.

(SemanticCache and EmbeddingCache were removed in the v3-thin-proxy era —
Claude Code is the orchestrator now and request-level dedup is its job. The
semantic cache also distorted routing/observability: cached responses could
report a different model than the caller asked for, wirelogs lied about
latencies, and tool_calls in cached responses replayed against possibly-
different tool inventories. ToolResultCache is a different shape entirely —
key-value, hash-keyed, deterministic — and stays.)
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Optional


class ToolResultCache:
    """
    Short-TTL cache for deterministic tool calls.

    Cache tools whose results are stable for a few minutes (web search,
    calculator, datetime).  Tools whose results must always be fresh (memory
    search, system_info) should not use this cache.
    """

    def __init__(self, ttl: float = 300.0, max_size: int = 200):
        self._store: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._ttl = ttl
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _key(tool: str, query: str) -> str:
        return hashlib.sha256(f"{tool}:{query}".encode()).hexdigest()[:32]

    def get(self, tool: str, query: str) -> Optional[str]:
        k = self._key(tool, query)
        entry = self._store.get(k)
        if entry is None:
            self._misses += 1
            return None
        result, ts = entry
        if time.time() - ts > self._ttl:
            del self._store[k]
            self._misses += 1
            return None
        self._hits += 1
        return result

    def put(self, tool: str, query: str, result: str) -> None:
        k = self._key(tool, query)
        if k in self._store:
            self._store.move_to_end(k)
        self._store[k] = (result, time.time())
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
            "size": len(self._store),
            "ttl_seconds": self._ttl,
        }
