"""
MemoryBackend — in-memory implementation of VectorBackend.

Dict-based storage with brute-force cosine kNN. Useful for tests and ephemeral
runs where persistence is not required.

Not for production: forgets everything on restart and scales O(N) per query.
For real workloads use PostgresBackend.

Thread-safety: a single threading.Lock guards both the store and the detector
state, matching the convention used by ChromaBackend / PostgresBackend.
"""

import logging
import threading

import numpy as np

from .base import VectorBackend
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector

logger = logging.getLogger(__name__)


class MemoryBackend(VectorBackend):
    """In-memory vector store with brute-force cosine search."""

    def __init__(
        self,
        rag_detector: RAGPoisoningDetector | None = None,
        detection_mode: str = "warn",
        **_unused,
    ):
        """
        Initialize an empty in-memory store.

        Args:
            rag_detector: RAGPoisoningDetector instance (created if None).
            detection_mode: "warn", "quarantine", or "strict".
            **_unused: Accept and ignore extra kwargs (e.g. `path=`) so this
                backend is a drop-in replacement for tests previously calling
                `make_backend("chromadb", path=tmpdir)`.
        """
        self._lock = threading.Lock()
        # id → (embedding: np.ndarray, document: str, metadata: dict)
        self._store: dict[str, tuple[np.ndarray, str, dict]] = {}

        self._detector = rag_detector or RAGPoisoningDetector()
        self._detection_mode = detection_mode
        self._quarantine_count = 0

        logger.info(
            "MemoryBackend initialised (in-memory, rag_detection=%s, mode=%s)",
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
        for vid, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            is_poisoned, confidence, reason = self._detector.is_poisoned(emb)

            if is_poisoned:
                msg = (
                    f"RAG poisoning detected in embedding {vid}: {reason} "
                    f"(confidence={confidence:.2f})"
                )
                logger.warning(msg)

                # Emit before applying the action so strict-mode raises
                # still produce a wire event.
                self._detector.emit_anomaly_event(
                    action=self._detection_mode,
                    confidence=confidence,
                    reason=reason,
                    vector_id=vid,
                    backend="memory",
                )

                if self._detection_mode == "warn":
                    pass  # fall through to store
                elif self._detection_mode == "quarantine":
                    with self._lock:
                        self._quarantine_count += 1
                    continue
                elif self._detection_mode == "strict":
                    raise ValueError(msg)
            else:
                self._detector.update_baseline(emb)

            arr = np.asarray(emb, dtype=np.float32)
            with self._lock:
                self._store[vid] = (arr, doc, dict(meta) if meta else {})

    def query(
        self,
        embedding: list[float],
        n_results: int,
        where: dict | None = None,
    ) -> dict:
        """
        Nearest-neighbour similarity search via brute-force cosine distance.

        Returns a dict matching the ChromaDB collection.query() shape:
            {"ids":       [[...]],
             "documents":  [[...]],
             "metadatas":  [[...]],
             "distances":  [[...]]}

        Distances are cosine distances in [0, 2]; smaller = more similar.
        """
        empty = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

        with self._lock:
            items = list(self._store.items())

        if not items:
            return empty

        # Apply metadata filter (Chroma-style equality match on each key)
        if where:
            items = [
                (vid, payload)
                for vid, payload in items
                if all(payload[2].get(k) == v for k, v in where.items())
            ]
            if not items:
                return empty

        query_vec = np.asarray(embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(query_vec))
        if q_norm == 0.0:
            # Degenerate query — return empty rather than NaN distances
            return empty

        scored: list[tuple[float, str, str, dict]] = []
        for vid, (vec, doc, meta) in items:
            v_norm = float(np.linalg.norm(vec))
            if v_norm == 0.0:
                distance = 1.0
            else:
                similarity = float(np.dot(query_vec, vec) / (q_norm * v_norm))
                # Clamp to handle floating-point drift outside [-1, 1]
                similarity = max(-1.0, min(1.0, similarity))
                distance = 1.0 - similarity
            scored.append((distance, vid, doc, meta))

        scored.sort(key=lambda x: x[0])
        top = scored[:n_results]

        return {
            "ids":       [[t[1] for t in top]],
            "documents": [[t[2] for t in top]],
            "metadatas": [[t[3] for t in top]],
            "distances": [[t[0] for t in top]],
        }

    def count(self) -> int:
        with self._lock:
            return len(self._store)

    def get_detector_stats(self) -> dict:
        """RAG poisoning detector statistics — parity with the other backends."""
        return {
            "detector": self._detector.get_baseline_stats(),
            "quarantine_count": self._quarantine_count,
            "detection_mode": self._detection_mode,
        }
