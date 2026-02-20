"""
Tests for semantic conversation map.
Run with: pytest tests/test_semantic_map.py

Note: These tests mock the embedding calls since they require a running
Ollama instance. Integration tests with real embeddings should be run
separately.
"""

import pytest
import math
from unittest.mock import MagicMock, patch

from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.models import Message
from beigebox.semantic_map import SemanticMap, _cosine_similarity_pure


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def test_cosine_similarity_identical():
    """Identical vectors have similarity 1.0."""
    v = [1.0, 2.0, 3.0]
    assert _cosine_similarity_pure(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    """Orthogonal vectors have similarity 0.0."""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert _cosine_similarity_pure(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite():
    """Opposite vectors have similarity -1.0."""
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert _cosine_similarity_pure(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector():
    """Zero vector returns 0.0 (not NaN)."""
    a = [0.0, 0.0]
    b = [1.0, 2.0]
    assert _cosine_similarity_pure(a, b) == 0.0


# ---------------------------------------------------------------------------
# SemanticMap
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "test.db"))


@pytest.fixture
def mock_vector():
    """Mock vector store that returns deterministic embeddings."""
    vs = MagicMock()
    # Return embeddings based on content hash for deterministic similarity
    call_count = [0]
    def fake_embedding(text):
        call_count[0] += 1
        # Simple deterministic embedding: hash-based
        h = hash(text) % 1000
        return [float(h % 10) / 10, float((h // 10) % 10) / 10, float((h // 100) % 10) / 10]
    vs._get_embedding = fake_embedding
    vs.collection = MagicMock()
    vs.collection.get.side_effect = Exception("not found")  # Force re-embedding
    return vs


def test_semantic_map_empty_conversation(store, mock_vector):
    """Empty conversation returns empty map."""
    mapper = SemanticMap(sqlite=store, vector=mock_vector)
    result = mapper.build("nonexistent")
    assert result["topics"] == []
    assert result["edges"] == []


def test_semantic_map_single_message(store, mock_vector):
    """Single message produces one topic, no edges."""
    store.store_message(Message(conversation_id="c1", role="user", content="What is Docker?"))

    mapper = SemanticMap(sqlite=store, vector=mock_vector)
    result = mapper.build("c1")

    assert len(result["topics"]) == 1
    assert result["topics"][0]["text"].startswith("What is Docker")
    assert result["edges"] == []
    assert len(result["clusters"]) == 1


def test_semantic_map_filters_assistant_messages(store, mock_vector):
    """Only user messages become topics."""
    store.store_message(Message(conversation_id="c1", role="user", content="question"))
    store.store_message(Message(conversation_id="c1", role="assistant", content="answer"))
    store.store_message(Message(conversation_id="c1", role="user", content="follow-up"))

    mapper = SemanticMap(sqlite=store, vector=mock_vector)
    result = mapper.build("c1")

    assert len(result["topics"]) == 2
    roles = [t["text"] for t in result["topics"]]
    assert any("question" in r for r in roles)
    assert any("follow-up" in r for r in roles)


def test_semantic_map_edges_above_threshold(store):
    """Edges only appear when similarity exceeds threshold."""
    vs = MagicMock()
    vs.collection = MagicMock()
    vs.collection.get.side_effect = Exception("not found")

    # Two very similar embeddings and one different
    embeddings = iter([
        [1.0, 0.0, 0.0],  # topic 0
        [0.99, 0.1, 0.0],  # topic 1 — very similar to 0
        [0.0, 0.0, 1.0],  # topic 2 — very different
    ])
    vs._get_embedding = lambda text: next(embeddings)

    store.store_message(Message(conversation_id="c1", role="user", content="Docker basics"))
    store.store_message(Message(conversation_id="c1", role="user", content="Docker advanced"))
    store.store_message(Message(conversation_id="c1", role="user", content="Python async"))

    mapper = SemanticMap(sqlite=store, vector=vs, similarity_threshold=0.8)
    result = mapper.build("c1")

    # Should have edge between 0 and 1 (similar), but not with 2
    edges = result["edges"]
    edge_pairs = [(e["from"], e["to"]) for e in edges]
    assert (0, 1) in edge_pairs
    assert (0, 2) not in edge_pairs
    assert (1, 2) not in edge_pairs


def test_semantic_map_clustering(store):
    """Connected topics form clusters."""
    vs = MagicMock()
    vs.collection = MagicMock()
    vs.collection.get.side_effect = Exception("not found")

    # Two clusters: {0,1} similar to each other, {2,3} similar to each other
    embeddings = iter([
        [1.0, 0.0],  # 0
        [0.95, 0.05],  # 1 — similar to 0
        [0.0, 1.0],  # 2
        [0.05, 0.95],  # 3 — similar to 2
    ])
    vs._get_embedding = lambda text: next(embeddings)

    for i in range(4):
        store.store_message(Message(conversation_id="c1", role="user", content=f"topic {i}"))

    mapper = SemanticMap(sqlite=store, vector=vs, similarity_threshold=0.8)
    result = mapper.build("c1")

    clusters = result["clusters"]
    assert len(clusters) == 2
    # Each cluster should have 2 topics
    sizes = sorted([c["size"] for c in clusters])
    assert sizes == [2, 2]


def test_semantic_map_max_topics(store, mock_vector):
    """Topics are capped at max_topics."""
    for i in range(10):
        store.store_message(Message(conversation_id="c1", role="user", content=f"topic {i}"))

    mapper = SemanticMap(sqlite=store, vector=mock_vector, max_topics=3)
    result = mapper.build("c1")
    assert len(result["topics"]) <= 3


def test_semantic_map_visualization(store, mock_vector):
    """Visualization string is generated."""
    store.store_message(Message(conversation_id="c1", role="user", content="What is Docker?"))
    store.store_message(Message(conversation_id="c1", role="user", content="How does K8s work?"))

    mapper = SemanticMap(sqlite=store, vector=mock_vector)
    result = mapper.build("c1")
    assert "SEMANTIC MAP" in result["visualization"]


def test_semantic_map_stats(store, mock_vector):
    """Stats are computed correctly."""
    for i in range(3):
        store.store_message(Message(conversation_id="c1", role="user", content=f"question {i}"))

    mapper = SemanticMap(sqlite=store, vector=mock_vector, similarity_threshold=0.0)
    result = mapper.build("c1")

    assert result["stats"]["topic_count"] == 3
    assert result["stats"]["cluster_count"] >= 1


def test_semantic_map_strips_embeddings_from_output(store, mock_vector):
    """Embeddings are removed from topic output (they're huge)."""
    store.store_message(Message(conversation_id="c1", role="user", content="test"))

    mapper = SemanticMap(sqlite=store, vector=mock_vector)
    result = mapper.build("c1")

    for topic in result["topics"]:
        assert "embedding" not in topic
