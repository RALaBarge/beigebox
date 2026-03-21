"""
BeigeBox cache layer — three complementary caches that share one module.

SemanticCache
    Caches assistant responses keyed by semantic similarity of the user query.
    When a new message is sufficiently similar to a cached one (cosine ≥ threshold),
    the cached response is returned immediately, bypassing the backend entirely.

EmbeddingCache
    Short-lived in-process dedup for computed embeddings.  Both the classifier
    and the semantic cache need to embed the same message — this avoids two
    identical HTTP round-trips to Ollama.

ToolResultCache
    Short-TTL dict for deterministic tool calls (web_search, calculator, etc.).
    Keyed by SHA-256(tool_name + query); TTL default 300 s.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

import httpx
import numpy as np

from beigebox.logging import log_cache_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EmbeddingCache
# ---------------------------------------------------------------------------

class EmbeddingCache:
    """
    In-process cache for embedding vectors.

    Avoids re-embedding the same text within a session.  Not persisted —
    intentionally ephemeral; the embedding model is deterministic so a fresh
    boot just warms up again quickly.
    """

    def __init__(self, max_size: int = 1000, ttl: float = 300.0):
        self._store: OrderedDict[str, tuple[np.ndarray, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def get(self, text: str) -> Optional[np.ndarray]:
        entry = self._store.get(text)
        if entry is None:
            return None
        vec, ts = entry
        if time.time() - ts > self._ttl:
            del self._store[text]
            return None
        self._store.move_to_end(text)
        return vec

    def put(self, text: str, vec: np.ndarray) -> None:
        if text in self._store:
            self._store.move_to_end(text)
        self._store[text] = (vec, time.time())
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# ToolResultCache
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SemanticCache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    embedding: np.ndarray
    response: str
    model: str
    user_message: str
    ts: float = field(default_factory=time.time)


class SemanticCache:
    """
    Cache assistant responses by semantic similarity of the user query.

    Lookup flow:
      1. Embed the incoming user message (async, deduped via EmbeddingCache).
      2. Compute cosine similarity against all stored entries (vectorised).
      3. If best similarity ≥ threshold and entry is within TTL → cache hit.

    Store flow (call after a successful backend response):
      - The embedding computed at lookup step 1 is already in EmbeddingCache,
        so storage is O(1) dict lookup + list append.
      - If somehow the embedding isn't available, the entry is silently skipped
        rather than making a blocking HTTP call in the hot path.

    Config keys (under ``semantic_cache:``):
      enabled            bool   false  — master switch
      similarity_threshold  float  0.92  — minimum cosine similarity for a hit
      max_entries        int    500   — LRU-evict oldest when full
      ttl_seconds        float  3600  — entries older than this are ignored
    """

    def __init__(self, cfg: dict):
        sc_cfg = cfg.get("semantic_cache", {})
        self.enabled: bool = sc_cfg.get("enabled", False)
        self.threshold: float = float(sc_cfg.get("similarity_threshold", 0.92))
        self.max_entries: int = int(sc_cfg.get("max_entries", 500))
        self.ttl: float = float(sc_cfg.get("ttl_seconds", 3600))

        embed_cfg = cfg.get("embedding", {})
        self._embed_model = embed_cfg.get("model", "nomic-embed-text")
        self._embed_url = embed_cfg.get(
            "backend_url", cfg["backend"]["url"]
        ).rstrip("/")

        self._entries: list[_CacheEntry] = []
        self._hits = 0
        self._misses = 0
        self._embedding_cache = EmbeddingCache()
        self._last_eviction: float = 0.0
        self._eviction_interval: float = 60.0

        if self.enabled:
            logger.info(
                "SemanticCache enabled (threshold=%.2f, max=%d, ttl=%ds)",
                self.threshold, self.max_entries, int(self.ttl),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Async embedding lookup — checks EmbeddingCache first."""
        cached = self._embedding_cache.get(text)
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self._embed_url}/api/embed",
                    json={"model": self._embed_model, "input": text},
                )
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings", [[]])
                if embeddings and embeddings[0]:
                    vec = np.array(embeddings[0], dtype=np.float32)
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        vec = vec / norm
                    self._embedding_cache.put(text, vec)
                    return vec
        except Exception as e:
            logger.debug("SemanticCache embed error: %s", e)
        return None

    def _evict_expired(self) -> None:
        now = time.time()
        if now - self._last_eviction < self._eviction_interval:
            return
        cutoff = now - self.ttl
        write_idx = 0
        for entry in self._entries:
            if entry.ts >= cutoff:
                self._entries[write_idx] = entry
                write_idx += 1
        del self._entries[write_idx:]
        self._last_eviction = now

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup(self, user_message: str) -> Optional[tuple[str, str]]:
        """
        Returns (response_text, model) on a cache hit, else None.
        Always call this *before* routing/backend forwarding.
        """
        if not self.enabled or not user_message.strip():
            return None

        self._evict_expired()
        if not self._entries:
            self._misses += 1
            return None

        vec = await self._get_embedding(user_message)
        if vec is None:
            self._misses += 1
            return None

        # Vectorised cosine similarity: stack all stored embeddings into a
        # (N, D) matrix and compute dot products against the query vector in
        # one NumPy call. All embeddings were L2-normalised at store time so
        # dot product == cosine similarity. Faster than looping for even a
        # few hundred entries, and avoids per-entry Python overhead.
        matrix = np.stack([e.embedding for e in self._entries])  # (N, D)
        sims = matrix @ vec
        best_idx = int(np.argmax(sims))
        best_sim = float(sims[best_idx])

        if best_sim >= self.threshold:
            entry = self._entries[best_idx]
            self._hits += 1
            logger.info(
                "SemanticCache HIT sim=%.3f model=%s msg=%.50s",
                best_sim, entry.model, user_message,
            )
            log_cache_event(
                event_type="hit",
                cache_type="semantic",
                key=user_message[:50],
                similarity=best_sim,
                ttl_remaining=int(entry.expires_at - time.time()),
            )
            return entry.response, entry.model

        self._misses += 1
        logger.debug("SemanticCache MISS best_sim=%.3f", best_sim)
        log_cache_event(
            event_type="miss",
            cache_type="semantic",
            key=user_message[:50],
            similarity=best_sim,
        )
        return None

    def store(self, user_message: str, response: str, model: str) -> None:
        """
        Store a (message, response) pair.  The embedding must already be in
        EmbeddingCache from the earlier lookup() call; if it isn't, the entry
        is skipped silently (never blocks the hot path).
        """
        if not self.enabled or not user_message.strip() or not response.strip():
            return

        embedding = self._embedding_cache.get(user_message)
        if embedding is None:
            logger.debug("SemanticCache: no embedding available for store, skipping")
            return

        self._evict_expired()
        if len(self._entries) >= self.max_entries:
            self._entries.pop(0)  # evict oldest

        self._entries.append(_CacheEntry(
            embedding=embedding,
            response=response,
            model=model,
            user_message=user_message,
        ))
        logger.debug("SemanticCache stored (total=%d)", len(self._entries))
        log_cache_event(
            event_type="store",
            cache_type="semantic",
            key=user_message[:50],
            ttl_remaining=self.ttl,
        )

    def stats(self) -> dict:
        self._evict_expired()
        total = self._hits + self._misses
        return {
            "enabled": self.enabled,
            "entries": len(self._entries),
            "max_entries": self.max_entries,
            "threshold": self.threshold,
            "ttl_seconds": self.ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
            "embedding_cache_size": self._embedding_cache.size(),
        }
