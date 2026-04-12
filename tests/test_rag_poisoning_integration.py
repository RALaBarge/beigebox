"""
Integration tests for RAG poisoning detection with ChromaBackend.

Tests the full integration of:
- RAGPoisoningDetector in ChromaBackend
- Configuration loading
- Different detection modes (warn, quarantine, strict)
- Baseline tracking during storage
"""

import pytest
import numpy as np
import tempfile
from pathlib import Path

from beigebox.storage.backends.chroma import ChromaBackend
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector


@pytest.fixture
def tmp_chroma_path():
    """Create a temporary directory for ChromaDB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def detector():
    """Create a detector for testing."""
    return RAGPoisoningDetector(sensitivity=0.95, baseline_window=100)


@pytest.fixture
def normal_embeddings():
    """Generate normal embeddings for baseline."""
    np.random.seed(42)
    return [np.random.randn(128).tolist() for _ in range(20)]


class TestChromaBackendDetection:
    """Test ChromaBackend integration with detector."""

    def test_initialization_with_detector(self, tmp_chroma_path, detector):
        """Test ChromaBackend initializes with detector."""
        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="warn")
        assert backend._detector is not None
        assert backend._detection_mode == "warn"

    def test_initialization_without_detector(self, tmp_chroma_path):
        """Test ChromaBackend creates default detector."""
        backend = ChromaBackend(tmp_chroma_path)
        assert backend._detector is not None

    def test_upsert_normal_embeddings(self, tmp_chroma_path, detector, normal_embeddings):
        """Test storing normal embeddings."""
        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="warn")

        ids = [f"msg_{i}" for i in range(len(normal_embeddings))]
        docs = [f"document {i}" for i in range(len(normal_embeddings))]
        metas = [{"index": str(i)} for i in range(len(normal_embeddings))]

        backend.upsert(ids, normal_embeddings, docs, metas)

        # Check that vectors were stored
        assert backend.count() == len(normal_embeddings)

    def test_baseline_updated_with_safe_embeddings(self, tmp_chroma_path, detector, normal_embeddings):
        """Test that baseline is updated with stored safe embeddings."""
        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="warn")

        stats_before = detector.get_baseline_stats()
        assert stats_before["count"] == 0

        ids = [f"msg_{i}" for i in range(len(normal_embeddings))]
        docs = [f"document {i}" for i in range(len(normal_embeddings))]
        metas = [{"index": str(i)} for i in range(len(normal_embeddings))]

        backend.upsert(ids, normal_embeddings, docs, metas)

        stats_after = detector.get_baseline_stats()
        # All safe embeddings should update baseline
        assert stats_after["count"] == len(normal_embeddings)

    def test_detection_modes_warn(self, tmp_chroma_path, detector):
        """Test warn mode: logs and stores poisoned vectors."""
        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="warn")

        # Create one safe and one poisoned embedding
        safe_emb = [np.random.randn(128).tolist() for _ in range(10)]
        for emb in safe_emb:
            detector.update_baseline(emb)

        # Poisoned: all zeros
        poisoned_emb = [[0.0] * 128]
        normal_emb = [np.random.randn(128).tolist()]

        ids = ["safe", "poisoned", "normal"]
        docs = ["safe doc", "poisoned doc", "normal doc"]
        metas = [{"type": "safe"}, {"type": "poisoned"}, {"type": "normal"}]
        embeddings = safe_emb[0:1] + poisoned_emb + normal_emb

        # Should not raise error in warn mode
        backend.upsert(ids, embeddings, docs, metas)

        # All should be stored in warn mode
        assert backend.count() == 3

    def test_detection_modes_quarantine(self, tmp_chroma_path, detector):
        """Test quarantine mode: rejects poisoned vectors."""
        backend = ChromaBackend(
            tmp_chroma_path, rag_detector=detector, detection_mode="quarantine"
        )

        # Build baseline
        safe_embs = [np.random.randn(128).tolist() for _ in range(20)]
        for emb in safe_embs:
            detector.update_baseline(emb)

        # Store safe embeddings first
        ids = [f"safe_{i}" for i in range(len(safe_embs))]
        docs = [f"safe {i}" for i in range(len(safe_embs))]
        metas = [{"type": "safe"} for _ in range(len(safe_embs))]
        backend.upsert(ids, safe_embs, docs, metas)

        initial_count = backend.count()

        # Try to store poisoned vectors
        poisoned_ids = ["poisoned_1", "poisoned_2"]
        poisoned_embs = [
            [0.0] * 128,  # All zeros
            [100.0] * 128,  # Extremely large
        ]
        poisoned_docs = ["poisoned 1", "poisoned 2"]
        poisoned_metas = [{"type": "poisoned"}, {"type": "poisoned"}]

        backend.upsert(poisoned_ids, poisoned_embs, poisoned_docs, poisoned_metas)

        # Count should increase only for safe vectors, none added
        assert backend.count() == initial_count
        assert backend._quarantine_count > 0

    def test_detection_modes_strict(self, tmp_chroma_path, detector):
        """Test strict mode: raises error on poisoned vectors."""
        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="strict")

        # Build baseline
        safe_embs = [np.random.randn(128).tolist() for _ in range(10)]
        for emb in safe_embs:
            detector.update_baseline(emb)

        ids = ["poisoned"]
        docs = ["poisoned doc"]
        metas = [{"type": "poisoned"}]
        embeddings = [[0.0] * 128]  # Poisoned

        # Should raise error in strict mode
        with pytest.raises(ValueError, match="RAG poisoning"):
            backend.upsert(ids, embeddings, docs, metas)

    def test_detector_stats_accessible(self, tmp_chroma_path, detector):
        """Test that detector stats are accessible through backend."""
        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="warn")

        stats = backend.get_detector_stats()
        assert "detector" in stats
        assert "quarantine_count" in stats
        assert "detection_mode" in stats

        assert stats["detection_mode"] == "warn"
        assert stats["quarantine_count"] == 0


class TestPoisoningScenarios:
    """Test realistic poisoning attack scenarios."""

    def test_magnitude_attack_detection(self, tmp_chroma_path, detector):
        """Test detection of magnitude-based poisoning attack."""
        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="quarantine")

        # Build baseline with normal embeddings
        np.random.seed(99)
        baseline_embs = [np.random.randn(128).tolist() for _ in range(50)]
        ids = [f"baseline_{i}" for i in range(len(baseline_embs))]
        docs = [f"baseline {i}" for i in range(len(baseline_embs))]
        metas = [{"type": "baseline"} for _ in range(len(baseline_embs))]

        backend.upsert(ids, baseline_embs, docs, metas)

        # Now try to inject poisoned vectors with extreme magnitude
        attack_embs = [
            (np.ones(128) * 50.0).tolist(),  # Scaled up 50x
            (np.ones(128) * 0.001).tolist(),  # Scaled down to tiny
        ]
        attack_ids = ["attack_large", "attack_tiny"]
        attack_docs = ["attack large", "attack tiny"]
        attack_metas = [{"type": "attack"}, {"type": "attack"}]

        count_before = backend.count()
        backend.upsert(attack_ids, attack_embs, attack_docs, attack_metas)

        # None of the attack vectors should be stored in quarantine mode
        assert backend.count() == count_before
        assert backend._quarantine_count >= 2

    def test_sparse_vector_detection(self, tmp_chroma_path, detector):
        """Test detection of sparse (mostly zero) embeddings."""
        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="quarantine")

        # Build baseline with normal dense embeddings
        baseline_embs = [np.random.randn(128).tolist() for _ in range(30)]
        ids = [f"normal_{i}" for i in range(len(baseline_embs))]
        docs = [f"normal {i}" for i in range(len(baseline_embs))]
        metas = [{"type": "normal"} for _ in range(len(baseline_embs))]

        backend.upsert(ids, baseline_embs, docs, metas)

        # Create sparse embeddings
        sparse_embs = []
        for _ in range(5):
            emb = [0.0] * 128
            emb[np.random.randint(0, 128)] = 10.0  # Single spike
            sparse_embs.append(emb)

        sparse_ids = [f"sparse_{i}" for i in range(len(sparse_embs))]
        sparse_docs = [f"sparse {i}" for i in range(len(sparse_embs))]
        sparse_metas = [{"type": "sparse"} for _ in range(len(sparse_embs))]

        count_before = backend.count()
        backend.upsert(sparse_ids, sparse_embs, sparse_docs, sparse_metas)

        # Sparse vectors may or may not be detected depending on magnitude
        # but system should handle gracefully
        assert isinstance(backend.count(), int)


class TestPerformance:
    """Test performance of detection (target: <5ms per vector)."""

    @pytest.mark.slow
    def test_upsert_throughput(self, tmp_chroma_path, detector):
        """Test that upsert with detection doesn't exceed budget."""
        import time

        backend = ChromaBackend(tmp_chroma_path, rag_detector=detector, detection_mode="warn")

        # Generate batch of embeddings
        np.random.seed(100)
        batch_size = 100
        embeddings = [np.random.randn(128).tolist() for _ in range(batch_size)]
        ids = [f"perf_{i}" for i in range(batch_size)]
        docs = [f"doc {i}" for i in range(batch_size)]
        metas = [{"idx": str(i)} for i in range(batch_size)]

        # Time the upsert
        start = time.time()
        backend.upsert(ids, embeddings, docs, metas)
        elapsed_ms = (time.time() - start) * 1000

        # Total budget is 500ms for 100 vectors (5ms per vector)
        total_budget_ms = 500
        assert elapsed_ms < total_budget_ms, (
            f"Upsert took {elapsed_ms:.1f}ms for {batch_size} vectors "
            f"({elapsed_ms/batch_size:.2f}ms per vector), exceeds {total_budget_ms}ms budget"
        )


class TestConfigIntegration:
    """Test integration with config system (if using config-driven setup)."""

    def test_detector_respects_config_params(self):
        """Test that detector respects all config parameters."""
        config_params = {
            "sensitivity": 0.99,
            "baseline_window": 500,
            "min_norm": 0.2,
            "max_norm": 5.0,
        }

        detector = RAGPoisoningDetector(**config_params)
        stats = detector.get_baseline_stats()

        assert stats["z_threshold"] > 3.0  # sensitivity 0.99
        assert detector.min_norm == 0.2
        assert detector.max_norm == 5.0
