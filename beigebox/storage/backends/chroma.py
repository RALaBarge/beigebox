"""
ChromaBackend — ChromaDB implementation of VectorBackend.

Wraps chromadb.PersistentClient.  All ChromaDB-specific imports and
calls live here; nothing outside this file needs to know about ChromaDB.

Thread safety: PersistentClient is not thread-safe by default.
All collection operations are serialised through a threading.Lock so
concurrent async tasks running in a thread pool can't race on the
collection handle.  asyncio tasks that call the sync upsert/query/count
methods from a threadpool executor will each acquire the lock.
"""

import logging
import threading
from pathlib import Path

import chromadb

from .base import VectorBackend

logger = logging.getLogger(__name__)


class ChromaBackend(VectorBackend):
    """ChromaDB-backed vector storage (thread-safe)."""

    def __init__(self, path: str):
        chroma_path = Path(path)
        chroma_path.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._collection = self._client.get_or_create_collection(
            name="conversations",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaBackend initialised (path=%s, thread-safe)", chroma_path)

    # ------------------------------------------------------------------
    # VectorBackend interface
    # ------------------------------------------------------------------

    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        with self._lock:
            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

    def query(
        self,
        embedding: list[float],
        n_results: int,
        where: dict | None = None,
    ) -> dict:
        kwargs: dict = dict(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        if where:
            kwargs["where"] = where
        with self._lock:
            return self._collection.query(**kwargs)

    def count(self) -> int:
        with self._lock:
            return self._collection.count()
