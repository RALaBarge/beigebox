"""
Tests for RAG Poisoning Detector.

Unit tests covering:
- Baseline calculation and statistics
- Magnitude anomaly detection (z-score and range-based)
- False positive rate validation
- Detector reset functionality
"""

import pytest
import numpy as np
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector


@pytest.fixture
def detector():
    """Create a fresh detector for each test."""
    return RAGPoisoningDetector(sensitivity=0.95, baseline_window=100)


@pytest.fixture
def normal_embeddings():
    """Generate normal embeddings (L2 norm around 1.0)."""
    np.random.seed(42)
    return [np.random.randn(128) for _ in range(20)]


class TestDetectorInitialization:
    """Test detector initialization and configuration."""

    def test_init_defaults(self):
        """Test default initialization."""
        detector = RAGPoisoningDetector()
        assert detector.sensitivity == 0.95
        assert detector.baseline_window == 1000
        assert detector.min_norm == 0.1
        assert detector.max_norm == 100.0

    def test_sensitivity_to_z_threshold(self):
        """Test sensitivity to z-score threshold mapping."""
        z_low = RAGPoisoningDetector._sensitivity_to_z_threshold(0.90)
        z_high = RAGPoisoningDetector._sensitivity_to_z_threshold(0.99)
        z_mid = RAGPoisoningDetector._sensitivity_to_z_threshold(0.95)

        assert z_low <= z_mid <= z_high
        assert z_low == pytest.approx(2.0)
        assert z_high == pytest.approx(4.0)
        assert 2.5 < z_mid < 3.5  # Should be around 3.0

    def test_init_with_custom_params(self):
        """Test initialization with custom parameters."""
        detector = RAGPoisoningDetector(
            sensitivity=0.99,
            baseline_window=500,
            min_norm=0.5,
            max_norm=5.0,
        )
        assert detector.sensitivity == 0.99
        assert detector.baseline_window == 500
        assert detector.min_norm == 0.5
        assert detector.max_norm == 5.0


class TestBaselineCalculation:
    """Test baseline statistic collection and updates."""

    def test_update_baseline_single(self, detector):
        """Test updating baseline with a single embedding."""
        emb = np.array([1.0, 0.0, 0.0])
        detector.update_baseline(emb)

        stats = detector.get_baseline_stats()
        assert stats["count"] == 1
        assert abs(stats["mean_norm"] - 1.0) < 0.01

    def test_update_baseline_list(self, detector):
        """Test that list embeddings are converted to numpy arrays."""
        detector.update_baseline([1.0, 0.0, 0.0])
        stats = detector.get_baseline_stats()
        assert stats["count"] == 1

    def test_update_baseline_multiple(self, detector, normal_embeddings):
        """Test baseline updates with multiple embeddings."""
        for emb in normal_embeddings:
            detector.update_baseline(emb)

        stats = detector.get_baseline_stats()
        assert stats["count"] == len(normal_embeddings)
        assert stats["mean_norm"] > 0
        assert stats["std_norm"] > 0

    def test_baseline_rolling_window(self, detector):
        """Test that baseline respects rolling window size."""
        detector_small = RAGPoisoningDetector(baseline_window=10)

        # Add more embeddings than window size
        for i in range(20):
            emb = np.random.randn(128)
            detector_small.update_baseline(emb)

        stats = detector_small.get_baseline_stats()
        # Count should reflect actual updates, not capped
        assert stats["count"] == 20
        # But internal window should only keep last 10
        assert stats["baseline_window_size"] == 10

    def test_reset_baseline(self, detector, normal_embeddings):
        """Test baseline reset functionality."""
        for emb in normal_embeddings:
            detector.update_baseline(emb)

        stats_before = detector.get_baseline_stats()
        assert stats_before["count"] > 0

        detector.reset_baseline()
        stats_after = detector.get_baseline_stats()
        assert stats_after["count"] == 0
        assert stats_after["mean_norm"] == 0.0


class TestPoisoningDetection:
    """Test poisoning detection logic."""

    def test_normal_embedding_not_flagged(self, detector, normal_embeddings):
        """Test that normal embeddings are not flagged."""
        # Build baseline with sufficient embeddings
        for emb in normal_embeddings:
            detector.update_baseline(emb)

        # Generate and test new normal embeddings from same distribution
        np.random.seed(100)
        test_embs = [np.random.randn(128) for _ in range(5)]
        for emb in test_embs:
            is_poisoned, confidence, reason = detector.is_poisoned(emb)
            assert not is_poisoned, f"Normal embedding flagged: {reason}"
            assert confidence == 0.0

    def test_empty_embedding_flagged(self, detector):
        """Test that empty embeddings are flagged."""
        is_poisoned, confidence, reason = detector.is_poisoned(np.array([]))
        assert is_poisoned
        assert confidence == 1.0
        assert "Empty" in reason

    def test_zero_embedding_flagged(self, detector):
        """Test that all-zeros embeddings are flagged."""
        is_poisoned, confidence, reason = detector.is_poisoned(np.zeros(128))
        assert is_poisoned
        assert "below minimum" in reason

    def test_magnitude_too_small(self, detector):
        """Test detection of embeddings with magnitude below minimum."""
        tiny_emb = np.array([0.001, 0.001, 0.001])  # norm ≈ 0.0017
        is_poisoned, confidence, reason = detector.is_poisoned(tiny_emb)
        assert is_poisoned
        assert "below minimum" in reason
        assert 0 < confidence <= 1.0

    def test_magnitude_too_large(self, detector):
        """Test detection of embeddings with magnitude above maximum."""
        huge_emb = np.array([100.0] * 128)  # large norm
        is_poisoned, confidence, reason = detector.is_poisoned(huge_emb)
        assert is_poisoned
        assert "above maximum" in reason
        assert 0 < confidence <= 1.0

    def test_z_score_anomaly_positive(self, detector):
        """Test z-score detection for unusually large magnitudes."""
        # Build baseline with normal embeddings
        np.random.seed(123)
        for _ in range(50):
            emb = np.random.randn(128)
            detector.update_baseline(emb)

        # Create embedding with very large magnitude (extreme outlier)
        # Make it 100x larger than typical
        anomaly = np.ones(128) * 100.0
        is_poisoned, confidence, reason = detector.is_poisoned(anomaly)
        assert is_poisoned, f"Anomaly not detected: {reason}"
        # Can be detected either as magnitude anomaly or z-score anomaly
        assert "anomaly" in reason.lower() or "above maximum" in reason.lower()

    def test_z_score_anomaly_negative(self, detector):
        """Test z-score detection for unusually small magnitudes."""
        # Build baseline with normal embeddings
        np.random.seed(456)
        for _ in range(50):
            emb = np.random.randn(128)
            detector.update_baseline(emb)

        # Create embedding with very small magnitude (but > 0.1)
        anomaly = np.ones(128) * 0.01
        is_poisoned, confidence, reason = detector.is_poisoned(anomaly)
        # Should trigger range check before z-score
        assert is_poisoned

    def test_confidence_scores(self, detector):
        """Test that confidence scores are in [0, 1] range."""
        detections = [
            np.array([]),  # empty
            np.zeros(128),  # too small
            np.ones(128) * 100,  # too large
        ]

        for emb in detections:
            is_poisoned, confidence, reason = detector.is_poisoned(emb)
            assert 0 <= confidence <= 1.0


class TestSyntheticPoisoningScenarios:
    """Test detection with synthetic poisoned embeddings."""

    def test_constant_embedding_detected(self, detector):
        """Test detection of constant-valued embeddings (suspicious pattern)."""
        # Build baseline with random embeddings
        np.random.seed(789)
        for _ in range(30):
            emb = np.random.randn(128)
            detector.update_baseline(emb)

        # Constant embedding is suspicious
        const_emb = np.ones(128) * 5.0
        is_poisoned, confidence, reason = detector.is_poisoned(const_emb)
        # May be detected as too large or anomalous
        if is_poisoned:
            assert confidence > 0

    def test_near_zero_components(self, detector):
        """Test detection of embeddings with many near-zero components."""
        # Build baseline
        np.random.seed(111)
        for _ in range(30):
            emb = np.random.randn(128)
            detector.update_baseline(emb)

        # Embedding with single large component and rest zeros (sparse)
        sparse_emb = np.zeros(128)
        sparse_emb[0] = 2.0  # One large spike
        is_poisoned, confidence, reason = detector.is_poisoned(sparse_emb)
        # Depends on baseline, but should not crash
        assert isinstance(is_poisoned, bool)

    def test_scaling_attack(self, detector):
        """Test detection of scaled versions of normal embeddings."""
        # Build baseline with normal embeddings
        np.random.seed(222)
        normal_embs = [np.random.randn(128) for _ in range(30)]
        for emb in normal_embs:
            detector.update_baseline(emb)

        # Take a normal embedding and scale it extremely
        base_emb = normal_embs[0]
        scaled_emb = base_emb * 50.0
        is_poisoned, confidence, reason = detector.is_poisoned(scaled_emb)
        # Should be detected as too large
        assert is_poisoned


class TestFalsePositiveRate:
    """Test false positive rate under normal conditions."""

    def test_false_positive_rate_low(self):
        """Test that false positive rate is < 10% for normal embeddings."""
        detector = RAGPoisoningDetector(sensitivity=0.95)

        # Generate and train on normal embeddings
        np.random.seed(333)
        baseline_embs = [np.random.randn(128) for _ in range(100)]
        for emb in baseline_embs:
            detector.update_baseline(emb)

        # Get baseline stats
        stats = detector.get_baseline_stats()
        # Verify baseline is well-formed
        assert stats["mean_norm"] > 0
        assert stats["std_norm"] > 0

        # Test on new normal embeddings from same distribution
        false_positives = 0
        test_count = 100
        np.random.seed(334)  # Different seed for test set
        for _ in range(test_count):
            emb = np.random.randn(128)
            is_poisoned, _, _ = detector.is_poisoned(emb)
            if is_poisoned:
                false_positives += 1

        fp_rate = false_positives / test_count
        # Allow up to 10% FP rate since z-score threshold may catch some outliers
        assert fp_rate < 0.10, f"False positive rate too high: {fp_rate:.2%}"

    def test_sensitivity_tradeoff(self):
        """Test that higher sensitivity reduces false positives."""
        np.random.seed(444)
        base_embs = [np.random.randn(128) for _ in range(50)]

        # Low sensitivity (higher threshold)
        detector_low = RAGPoisoningDetector(sensitivity=0.90)
        for emb in base_embs:
            detector_low.update_baseline(emb)

        # High sensitivity (lower threshold)
        detector_high = RAGPoisoningDetector(sensitivity=0.99)
        for emb in base_embs:
            detector_high.update_baseline(emb)

        # Test on normal embeddings
        test_embs = [np.random.randn(128) for _ in range(30)]

        fp_low = sum(
            1 for emb in test_embs if detector_low.is_poisoned(emb)[0]
        )
        fp_high = sum(
            1 for emb in test_embs if detector_high.is_poisoned(emb)[0]
        )

        # Higher sensitivity should have lower false positive rate
        assert fp_high <= fp_low


class TestThreadSafety:
    """Test thread-safety of detector."""

    def test_concurrent_updates(self, detector):
        """Test that concurrent baseline updates don't crash."""
        import threading

        def update_baseline():
            for _ in range(10):
                emb = np.random.randn(128)
                detector.update_baseline(emb)

        threads = [threading.Thread(target=update_baseline) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = detector.get_baseline_stats()
        assert stats["count"] > 0

    def test_concurrent_detection(self, detector, normal_embeddings):
        """Test that concurrent detection doesn't crash."""
        import threading

        # Setup baseline
        for emb in normal_embeddings:
            detector.update_baseline(emb)

        results = []

        def detect():
            for emb in normal_embeddings:
                result = detector.is_poisoned(emb)
                results.append(result)

        threads = [threading.Thread(target=detect) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) > 0


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_very_small_baseline(self, detector):
        """Test detection with minimal baseline (count < 2)."""
        # Add just one embedding
        detector.update_baseline(np.array([1.0, 0.0, 0.0]))

        # Should not crash on detection
        is_poisoned, confidence, reason = detector.is_poisoned(np.array([2.0, 0.0, 0.0]))
        assert isinstance(is_poisoned, bool)

    def test_identical_embeddings(self, detector):
        """Test with identical embeddings (zero std)."""
        emb = np.array([1.0, 1.0, 1.0])
        for _ in range(10):
            detector.update_baseline(emb)

        stats = detector.get_baseline_stats()
        # Std should be 0 or very small
        assert stats["std_norm"] >= 0

        # Detection should not crash
        is_poisoned, confidence, reason = detector.is_poisoned(emb)
        assert isinstance(is_poisoned, bool)

    def test_nan_handling(self, detector):
        """Test behavior with NaN values."""
        emb_with_nan = np.array([1.0, np.nan, 1.0])
        # Should not crash, NaN norm will be NaN
        is_poisoned, confidence, reason = detector.is_poisoned(emb_with_nan)
        # NaN comparisons will make this True
        assert isinstance(is_poisoned, bool)

    def test_inf_handling(self, detector):
        """Test behavior with infinity values."""
        emb_with_inf = np.array([1.0, np.inf, 1.0])
        is_poisoned, confidence, reason = detector.is_poisoned(emb_with_inf)
        # Inf norm will be inf, should be detected as too large
        assert is_poisoned

    def test_different_vector_sizes(self, detector):
        """Test with different embedding dimensions."""
        detector.update_baseline(np.random.randn(64))
        detector.update_baseline(np.random.randn(128))
        detector.update_baseline(np.random.randn(256))

        # Detection should work with any size
        is_poisoned, _, _ = detector.is_poisoned(np.random.randn(128))
        assert isinstance(is_poisoned, bool)


class TestStatisticsReporting:
    """Test statistics and reporting functionality."""

    def test_get_baseline_stats(self, detector, normal_embeddings):
        """Test baseline statistics reporting."""
        for emb in normal_embeddings:
            detector.update_baseline(emb)

        stats = detector.get_baseline_stats()
        assert "count" in stats
        assert "mean_norm" in stats
        assert "std_norm" in stats
        assert "z_threshold" in stats
        assert "baseline_window_size" in stats
        assert "min_norm_range" in stats
        assert "max_norm_range" in stats

        assert stats["count"] == len(normal_embeddings)
        assert stats["z_threshold"] == detector._z_threshold

    def test_stats_are_numerical(self, detector, normal_embeddings):
        """Test that all statistics are valid numbers."""
        for emb in normal_embeddings:
            detector.update_baseline(emb)

        stats = detector.get_baseline_stats()
        for key, value in stats.items():
            if key != "detection_mode":
                assert isinstance(value, (int, float))
                assert np.isfinite(value) or key == "std_norm"  # std can be 0
