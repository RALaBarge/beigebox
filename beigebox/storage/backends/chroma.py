"""
ChromaBackend — ChromaDB implementation of VectorBackend.

Wraps chromadb.PersistentClient.  All ChromaDB-specific imports and
calls live here; nothing outside this file needs to know about ChromaDB.

Thread safety note: PersistentClient is not thread-safe by default.
All collection operations are serialised through an asyncio lock via the
async helpers in VectorStore — this backend is called from a single
coroutine at a time in normal operation.
"""

import logging
from pathlib import Path

import chromadb

from .base import VectorBackend

logger = logging.getLogger(__name__)


class ChromaBackend(VectorBackend):
    """ChromaDB-backed vector storage."""

    def __init__(self, path: str):
        chroma_path = Path(path)
        chroma_path.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._collection = self._client.get_or_create_collection(
            name="conversations",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaBackend initialised (path=%s)", chroma_path)

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
        return self._collection.query(**kwargs)

    def count(self) -> int:
        return self._collection.count()
