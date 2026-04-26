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

import numpy as np

try:
    import chromadb
except ImportError as _chroma_err:
    raise ImportError(
        "chromadb is required for vector storage but is not installed. "
        "Install it with: pip install chromadb"
    ) from _chroma_err

from .base import VectorBackend
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector

logger = logging.getLogger(__name__)


class ChromaBackend(VectorBackend):
    """ChromaDB-backed vector storage (thread-safe)."""

    def __init__(
        self,
        path: str,
        rag_detector: RAGPoisoningDetector | None = None,
        detection_mode: str = "warn",
    ):
        """
        Initialize ChromaBackend with optional RAG poisoning detection.

        Args:
            path: Path to ChromaDB persistent storage.
            rag_detector: RAGPoisoningDetector instance (created if None).
            detection_mode: "warn", "quarantine", or "strict".
                - warn: log suspicious vectors but store them
                - quarantine: reject suspicious vectors with warning
                - strict: raise error on suspicious vectors
        """
        chroma_path = Path(path)
        chroma_path.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        # Single shared collection for all source types (conversations, tool
        # results, document chunks) — separated at query time via the
        # source_type metadata filter. hnsw:space=cosine means distances are
        # cosine distances in [0,2]; 0=identical, 2=orthogonal.
        self._collection = self._client.get_or_create_collection(
            name="conversations",
            metadata={"hnsw:space": "cosine"},
        )

        self._detector = rag_detector or RAGPoisoningDetector()
        self._detection_mode = detection_mode
        self._quarantine_count = 0

        logger.info(
            "ChromaBackend initialised (path=%s, thread-safe, "
            "rag_detection=%s, mode=%s)",
            chroma_path,
            self._detector is not None,
            self._detection_mode,
        )

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
        """
        Upsert vectors with optional RAG poisoning detection.

        If a vector is flagged as suspicious:
        - warn mode: log and store anyway
        - quarantine mode: skip storage (log warning)
        - strict mode: raise error
        """
        # Pre-check embeddings for poisoning
        safe_ids = []
        safe_embeddings = []
        safe_documents = []
        safe_metadatas = []

        for i, (vid, emb, doc, meta) in enumerate(
            zip(ids, embeddings, documents, metadatas)
        ):
            is_poisoned, confidence, reason = self._detector.is_poisoned(emb)

            if is_poisoned:
                msg = (
                    f"RAG poisoning detected in embedding {vid}: {reason} "
                    f"(confidence={confidence:.2f})"
                )
                logger.warning(msg)

                self._detector.emit_anomaly_event(
                    action=self._detection_mode,
                    confidence=confidence,
                    reason=reason,
                    vector_id=vid,
                    backend="chroma",
                )

                if self._detection_mode == "warn":
                    # Log and store anyway
                    safe_ids.append(vid)
                    safe_embeddings.append(emb)
                    safe_documents.append(doc)
                    safe_metadatas.append(meta)
                elif self._detection_mode == "quarantine":
                    # Skip this embedding
                    self._quarantine_count += 1
                    continue
                elif self._detection_mode == "strict":
                    # Raise error
                    raise ValueError(msg)
            else:
                safe_ids.append(vid)
                safe_embeddings.append(emb)
                safe_documents.append(doc)
                safe_metadatas.append(meta)
                # Also update baseline with safe embeddings
                self._detector.update_baseline(emb)

        # Store only safe (or warned) vectors
        if safe_ids:
            with self._lock:
                self._collection.upsert(
                    ids=safe_ids,
                    embeddings=safe_embeddings,
                    documents=safe_documents,
                    metadatas=safe_metadatas,
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

    def get_detector_stats(self) -> dict:
        """Get RAG poisoning detector statistics (for monitoring/debugging)."""
        return {
            "detector": self._detector.get_baseline_stats(),
            "quarantine_count": self._quarantine_count,
            "detection_mode": self._detection_mode,
        }
