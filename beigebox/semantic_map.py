"""
Semantic Conversation Map — topic clustering and visualization.

Uses existing ChromaDB embeddings to find topic clusters within a conversation.
Computes pairwise cosine similarity, builds a graph, and detects communities.

No external dependencies beyond what BeigeBox already uses (chromadb, numpy
is optional — falls back to pure Python if not available).
"""

from __future__ import annotations

import logging
import math
from beigebox.storage.vector_store import VectorStore
from beigebox.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

# Try numpy for fast cosine similarity; fall back to pure Python
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _cosine_similarity_pure(a: list[float], b: list[float]) -> float:
    """Pure Python cosine similarity."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _cosine_similarity(a, b) -> float:
    """Cosine similarity — uses numpy if available."""
    if HAS_NUMPY:
        a_arr = np.array(a)
        b_arr = np.array(b)
        dot = np.dot(a_arr, b_arr)
        norm = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
        return float(dot / norm) if norm > 0 else 0.0
    return _cosine_similarity_pure(a, b)


class SemanticMap:
    """Build a semantic topic map for a conversation."""

    def __init__(
        self,
        sqlite: SQLiteStore,
        vector: VectorStore,
        similarity_threshold: float = 0.5,
        max_topics: int = 50,
    ):
        self.sqlite = sqlite
        self.vector = vector
        self.threshold = similarity_threshold
        self.max_topics = max_topics

    def build(self, conversation_id: str) -> dict:
        """
        Build a semantic map for a conversation.

        Returns:
            {
                "conversation_id": "...",
                "topics": [...],
                "edges": [...],
                "clusters": [...],
                "visualization": "ASCII art"
            }
        """
        # Get messages from SQLite
        messages = self.sqlite.get_conversation(conversation_id)
        if not messages:
            return {
                "conversation_id": conversation_id,
                "error": "Conversation not found",
                "topics": [],
                "edges": [],
                "clusters": [],
            }

        # Filter to user messages (topics are driven by user queries)
        user_msgs = [m for m in messages if m["role"] == "user" and m["content"].strip()]
        if not user_msgs:
            return {
                "conversation_id": conversation_id,
                "topics": [],
                "edges": [],
                "clusters": [],
                "visualization": "(no user messages)",
            }

        # Limit to max_topics
        if len(user_msgs) > self.max_topics:
            user_msgs = user_msgs[:self.max_topics]

        # Get embeddings from ChromaDB
        topics = self._extract_topics(user_msgs)
        if len(topics) < 2:
            return {
                "conversation_id": conversation_id,
                "topics": topics,
                "edges": [],
                "clusters": [{"id": 0, "topics": [0], "size": 1}] if topics else [],
                "visualization": self._render(topics, [], []),
            }

        # Compute pairwise similarity
        edges = self._compute_edges(topics)

        # Detect clusters
        clusters = self._detect_clusters(topics, edges)

        return {
            "conversation_id": conversation_id,
            "topics": topics,
            "edges": edges,
            "clusters": clusters,
            "stats": {
                "topic_count": len(topics),
                "edge_count": len(edges),
                "cluster_count": len(clusters),
                "avg_similarity": (
                    round(sum(e["similarity"] for e in edges) / len(edges), 3)
                    if edges else 0
                ),
            },
            "visualization": self._render(topics, edges, clusters),
        }

    def _extract_topics(self, user_msgs: list[dict]) -> list[dict]:
        """
        Extract topics from user messages.
        Each message becomes a potential topic node.
        We fetch its embedding from ChromaDB for similarity computation.
        """
        topics = []
        for i, msg in enumerate(user_msgs):
            msg_id = msg.get("id", "")
            content = msg["content"]

            # Try to get embedding from ChromaDB
            embedding = None
            if msg_id:
                try:
                    result = self.vector.collection.get(
                        ids=[msg_id],
                        include=["embeddings"],
                    )
                    if result["embeddings"] and result["embeddings"][0]:
                        embedding = result["embeddings"][0]
                except Exception:
                    pass

            # If no stored embedding, generate one
            if embedding is None:
                try:
                    embedding = self.vector._get_embedding(content[:500])
                except Exception as e:
                    logger.warning("Failed to get embedding for topic %d: %s", i, e)
                    continue

            # Truncate content for display
            display = content[:100].replace("\n", " ").strip()
            if len(content) > 100:
                display += "..."

            topics.append({
                "id": i,
                "text": display,
                "full_text": content[:500],
                "message_id": msg_id,
                "timestamp": msg.get("timestamp", ""),
                "embedding": embedding,
                "cluster": -1,  # Assigned later
            })

        return topics

    def _compute_edges(self, topics: list[dict]) -> list[dict]:
        """Compute pairwise cosine similarity, keeping edges above threshold."""
        edges = []
        for i in range(len(topics)):
            emb_i = topics[i].get("embedding")
            if not emb_i:
                continue
            for j in range(i + 1, len(topics)):
                emb_j = topics[j].get("embedding")
                if not emb_j:
                    continue
                sim = _cosine_similarity(emb_i, emb_j)
                if sim >= self.threshold:
                    edges.append({
                        "from": i,
                        "to": j,
                        "similarity": round(sim, 4),
                    })
        return edges

    def _detect_clusters(self, topics: list[dict], edges: list[dict]) -> list[dict]:
        """
        Simple connected-components clustering.
        Topics connected by edges above threshold form a cluster.
        Uses union-find for efficiency.
        """
        n = len(topics)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for edge in edges:
            union(edge["from"], edge["to"])

        # Group by root
        cluster_map: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            cluster_map.setdefault(root, []).append(i)

        # Build cluster objects
        clusters = []
        for cluster_id, (_, topic_ids) in enumerate(
            sorted(cluster_map.items(), key=lambda x: -len(x[1]))
        ):
            # Assign cluster ID to topics
            for tid in topic_ids:
                topics[tid]["cluster"] = cluster_id

            # Compute intra-cluster cohesion
            cohesion = 0.0
            count = 0
            for edge in edges:
                if edge["from"] in topic_ids and edge["to"] in topic_ids:
                    cohesion += edge["similarity"]
                    count += 1
            avg_cohesion = round(cohesion / count, 4) if count else 0.0

            clusters.append({
                "id": cluster_id,
                "topics": topic_ids,
                "size": len(topic_ids),
                "cohesion": avg_cohesion,
            })

        return clusters

    def _render(self, topics: list[dict], edges: list[dict], clusters: list[dict]) -> str:
        """Render an ASCII visualization of the semantic map."""
        if not topics:
            return "(empty conversation)"

        lines = ["SEMANTIC MAP", ""]

        # Group by cluster
        for cluster in clusters:
            topic_ids = cluster["topics"]
            if len(topic_ids) == 0:
                continue

            cohesion = cluster.get("cohesion", 0)
            lines.append(f"  Cluster {cluster['id']} (size={cluster['size']}, cohesion={cohesion:.2f})")

            for tid in topic_ids:
                topic = topics[tid]
                text = topic["text"]
                lines.append(f"    [{tid}] {text}")

            # Show intra-cluster edges
            cluster_edges = [
                e for e in edges
                if e["from"] in topic_ids and e["to"] in topic_ids
            ]
            for edge in cluster_edges[:5]:  # Limit edges shown
                lines.append(
                    f"         [{edge['from']}] ──({edge['similarity']:.2f})── [{edge['to']}]"
                )

            lines.append("")

        # Cross-cluster edges
        cross_edges = []
        for edge in edges:
            from_cluster = topics[edge["from"]].get("cluster", -1)
            to_cluster = topics[edge["to"]].get("cluster", -1)
            if from_cluster != to_cluster:
                cross_edges.append(edge)

        if cross_edges:
            lines.append("  Cross-cluster links:")
            for edge in cross_edges[:10]:
                lines.append(
                    f"    [{edge['from']}] ──({edge['similarity']:.2f})── [{edge['to']}]"
                )
            lines.append("")

        lines.append(f"  Topics: {len(topics)} | Edges: {len(edges)} | Clusters: {len(clusters)}")

        # Strip embeddings from topics before returning (they're huge)
        for t in topics:
            t.pop("embedding", None)

        return "\n".join(lines)
