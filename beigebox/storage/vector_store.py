"""
ChromaDB vector storage for semantic search over conversations.
Embeds messages via Ollama's nomic-embed-text and stores in ChromaDB.
Metadata links back to SQLite records for full message retrieval.
"""

import logging
from pathlib import Path

import chromadb
import httpx

logger = logging.getLogger(__name__)


class VectorStore:
    """ChromaDB-backed vector store using Ollama embeddings."""

    def __init__(self, chroma_path: str, embedding_model: str, embedding_url: str):
        self.embedding_model = embedding_model
        self.embedding_url = embedding_url.rstrip("/")
        self.chroma_path = Path(chroma_path)
        self.chroma_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(path=str(self.chroma_path))
        self.collection = self.client.get_or_create_collection(
            name="conversations",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "VectorStore initialized (chroma=%s, model=%s)",
            self.chroma_path, self.embedding_model,
        )

    def _get_embedding(self, text: str) -> list[float]:
        """Get embedding vector from Ollama API."""
        resp = httpx.post(
            f"{self.embedding_url}/api/embed",
            json={"model": self.embedding_model, "input": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]

    async def _get_embedding_async(self, text: str) -> list[float]:
        """Async version for use in the request pipeline."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.embedding_url}/api/embed",
                json={"model": self.embedding_model, "input": text},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()["embeddings"][0]

    def store_message(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        model: str = "",
        timestamp: str = "",
    ):
        """Embed and store a message. Sync version for background tasks."""
        if not content.strip():
            return

        try:
            embedding = self._get_embedding(content)
            self.collection.upsert(
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
        """Embed and store a message. Async version."""
        if not content.strip():
            return

        try:
            embedding = await self._get_embedding_async(content)
            self.collection.upsert(
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

    def search(self, query: str, n_results: int = 5, role_filter: str | None = None) -> list[dict]:
        """Semantic search over stored messages."""
        embedding = self._get_embedding(query)

        where_filter = None
        if role_filter:
            where_filter = {"role": role_filter}

        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        hits = []
        for i in range(len(results["ids"][0])):
            hits.append({
                "id": results["ids"][0][i],
                "content": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
        return hits

    def get_stats(self) -> dict:
        """Return collection stats."""
        return {
            "total_embeddings": self.collection.count(),
        }
