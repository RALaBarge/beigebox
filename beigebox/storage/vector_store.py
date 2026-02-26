"""
VectorStore — embedding + semantic search facade.

Owns all embedding logic (calling Ollama's /api/embed endpoint).
Delegates raw vector storage to a pluggable VectorBackend.

The backend is configured via  storage.vector_backend  in config.yaml
(default: "chromadb").  All callers outside this module are unchanged —
they still instantiate VectorStore and call store_message / search /
search_grouped / get_stats.

To add a new vector database:
  1. Implement VectorBackend in beigebox/storage/backends/<n>.py
  2. Register it in beigebox/storage/backends/__init__.py
  3. Set  storage.vector_backend: <n>  in config.yaml
"""

import logging

import httpx

from beigebox.storage.backends import VectorBackend, make_backend

logger = logging.getLogger(__name__)


class VectorStore:
    """
    Embedding + semantic search facade over a pluggable VectorBackend.

    Embedding stays here; the backend only sees raw vectors.
    """

    def __init__(
        self,
        embedding_model: str,
        embedding_url: str,
        # Backend wiring — callers pass these through from config
        backend: VectorBackend | None = None,
        # Legacy convenience: accept chroma_path and build the default backend
        chroma_path: str | None = None,
    ):
        self.embedding_model = embedding_model
        self.embedding_url = embedding_url.rstrip("/")

        if backend is not None:
            self._backend = backend
        elif chroma_path is not None:
            # Legacy / convenience path: build ChromaBackend from path
            self._backend = make_backend("chromadb", path=chroma_path)
        else:
            raise ValueError(
                "VectorStore requires either a 'backend' instance or a 'chroma_path'."
            )

        logger.info(
            "VectorStore initialised (backend=%s, model=%s)",
            type(self._backend).__name__,
            self.embedding_model,
        )

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def _get_embedding(self, text: str) -> list[float]:
        """Get embedding vector from Ollama API (sync)."""
        try:
            resp = httpx.post(
                f"{self.embedding_url}/api/embed",
                json={"model": self.embedding_model, "input": text},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise RuntimeError(
                    f"Embedding model '{self.embedding_model}' not found — "
                    f"run: ollama pull {self.embedding_model}"
                ) from e
            raise
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(
                f"Embedding endpoint returned non-JSON response: {resp.text[:200]}"
            ) from e
        embeddings = data.get("embeddings")
        if not embeddings or not embeddings[0]:
            raise RuntimeError(
                f"Embedding model '{self.embedding_model}' returned an empty embeddings "
                f"array — input may be blank or the model may have failed silently."
            )
        return embeddings[0]

    async def _get_embedding_async(self, text: str) -> list[float]:
        """Get embedding vector from Ollama API (async)."""
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.embedding_url}/api/embed",
                    json={"model": self.embedding_model, "input": text},
                    timeout=30.0,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise RuntimeError(
                        f"Embedding model '{self.embedding_model}' not found — "
                        f"run: ollama pull {self.embedding_model}"
                    ) from e
                raise
            try:
                data = resp.json()
            except Exception as e:
                raise RuntimeError(
                    f"Embedding endpoint returned non-JSON response: {resp.text[:200]}"
                ) from e
            embeddings = data.get("embeddings")
            if not embeddings or not embeddings[0]:
                raise RuntimeError(
                    f"Embedding model '{self.embedding_model}' returned an empty embeddings "
                    f"array — input may be blank or the model may have failed silently."
                )
            return embeddings[0]

    # ------------------------------------------------------------------
    # Public API — unchanged from previous VectorStore
    # ------------------------------------------------------------------

    def store_message(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        model: str = "",
        timestamp: str = "",
    ):
        """Embed and store a message (sync, for background tasks)."""
        if not content.strip():
            return
        try:
            embedding = self._get_embedding(content)
            self._backend.upsert(
                ids=[message_id],
                embeddings=[embedding],
                documents=[content],
                metadatas=[{
                    "conversation_id": conversation_id,
                    "role": role,
                    "model": model,
                    "timestamp": timestamp,
                }],
            )
            logger.debug("Embedded message %s", message_id)
        except Exception as e:
            logger.error("Failed to embed message %s: %s", message_id, e)

    async def store_message_async(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        model: str = "",
        timestamp: str = "",
    ):
        """Embed and store a message (async, for the request pipeline)."""
        if not content.strip():
            return
        try:
            embedding = await self._get_embedding_async(content)
            self._backend.upsert(
                ids=[message_id],
                embeddings=[embedding],
                documents=[content],
                metadatas=[{
                    "conversation_id": conversation_id,
                    "role": role,
                    "model": model,
                    "timestamp": timestamp,
                }],
            )
            logger.debug("Embedded message %s", message_id)
        except Exception as e:
            logger.error("Failed to embed message %s: %s", message_id, e)

    def search(
        self,
        query: str,
        n_results: int = 5,
        role_filter: str | None = None,
    ) -> list[dict]:
        """Semantic search over stored messages."""
        try:
            embedding = self._get_embedding(query)
        except Exception as e:
            logger.error("search: failed to embed query: %s", e)
            return []

        where = {"role": role_filter} if role_filter else None
        results = self._backend.query(embedding, n_results=n_results, where=where)

        return [
            {
                "id":       results["ids"][0][i],
                "content":  results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            }
            for i in range(len(results["ids"][0]))
        ]

    def search_grouped(
        self,
        query: str,
        n_conversations: int = 5,
        candidates: int = 40,
        role_filter: str | None = None,
    ) -> list[dict]:
        """
        Semantic search grouped by conversation.

        Two-pass:
          1. Retrieve `candidates` message-level hits.
          2. Group by conversation_id, keep best (lowest distance) hit per
             conversation, sort by score, return top n_conversations.
        """
        try:
            embedding = self._get_embedding(query)
        except Exception as e:
            logger.error("search_grouped: failed to embed query: %s", e)
            return []

        where = {"role": role_filter} if role_filter else None
        fetch_n = min(max(candidates, n_conversations * 8), 200)
        results = self._backend.query(embedding, n_results=fetch_n, where=where)

        groups: dict[str, dict] = {}
        for i in range(len(results["ids"][0])):
            meta    = results["metadatas"][0][i]
            conv_id = meta.get("conversation_id", "")
            if not conv_id:
                continue
            distance = results["distances"][0][i]
            score    = max(0.0, round(1.0 - distance, 4))
            content  = results["documents"][0][i] or ""

            if conv_id not in groups:
                groups[conv_id] = {
                    "conversation_id": conv_id,
                    "score":           score,
                    "excerpt":         content[:300],
                    "role":            meta.get("role", ""),
                    "model":           meta.get("model", ""),
                    "timestamp":       meta.get("timestamp", ""),
                    "match_count":     1,
                }
            else:
                groups[conv_id]["match_count"] += 1
                if score > groups[conv_id]["score"]:
                    groups[conv_id].update({
                        "score":     score,
                        "excerpt":   content[:300],
                        "role":      meta.get("role", ""),
                        "model":     meta.get("model", ""),
                        "timestamp": meta.get("timestamp", ""),
                    })

        ranked = sorted(groups.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:n_conversations]

    def get_stats(self) -> dict:
        """Return collection stats."""
        return {"total_embeddings": self._backend.count()}
