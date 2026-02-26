"""
VectorBackend — abstract base for vector storage backends.

All backends must implement three primitives:
  upsert  — store a vector + document + metadata
  query   — nearest-neighbour search by vector
  count   — total stored vectors

Embedding logic stays in VectorStore (the caller), not here.
Backends are intentionally dumb: they only move vectors around.
"""

from abc import ABC, abstractmethod


class VectorBackend(ABC):
    """Abstract vector storage backend."""

    @abstractmethod
    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        """Insert or update vectors with associated documents and metadata."""
        ...

    @abstractmethod
    def query(
        self,
        embedding: list[float],
        n_results: int,
        where: dict | None = None,
    ) -> dict:
        """
        Nearest-neighbour search.

        Returns a dict with keys:
          ids        — list[list[str]]
          documents  — list[list[str]]
          metadatas  — list[list[dict]]
          distances  — list[list[float]]
        (matches the ChromaDB collection.query() shape so callers are identical)
        """
        ...

    @abstractmethod
    def count(self) -> int:
        """Return total number of stored vectors."""
        ...
