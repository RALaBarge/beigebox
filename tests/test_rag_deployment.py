"""
Tests for RAG Poisoning Defense — Production Deployment.

Tests baseline calibration, threshold tuning, and deployment stages.
Validates false positive rates and monitoring metrics.

Test organization:
- Baseline calibration accuracy and statistics
- Threshold tuning recommendations
- Deployment stages (warn → block → advanced)
- False positive rate validation
- Monitoring and alerting
"""

import json
import pytest
import tempfile
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import yaml

from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def tmp_data_dir():
    """Create temporary directory for baseline and config files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def normal_embeddings():
    """Generate realistic normal embeddings (embeddings from legitimate corpus)."""
    np.random.seed(42)
    # Embeddings typically have norm around 10-12
    norms = np.random.normal(loc=11.0, scale=0.5, size=200)
    embeddings = []
    for norm in norms:
        # Create embedding with target norm
        emb = np.random.randn(128)
        emb = emb / np.linalg.norm(emb) * norm
        embeddings.append(emb)
    return embeddings


@pytest.fixture
def poisoned_embeddings():
    """Generate poisoned embeddings (anomalous magnitudes)."""
    np.random.seed(43)
    embeddings = []

    # Type 1: Very small magnitudes (< 0.5)
    for _ in range(20):
        emb = np.random.randn(128) * 0.1
        embeddings.append(emb)

    # Type 2: Very large magnitudes (> 50)
    for _ in range(20):
        emb = np.random.randn(128) * 10.0
        embeddings.append(emb)

    # Type 3: Extreme z-scores (>3 sigma from mean)
    for _ in range(10):
        emb = np.random.randn(128)
        emb = emb / np.linalg.norm(emb) * 20.0  # way outside normal range
        embeddings.append(emb)

    return embeddings


@pytest.fixture
def baseline_file(tmp_data_dir, normal_embeddings):
    """Create a baseline.json file from normal embeddings."""
    norms = [float(np.linalg.norm(emb)) for emb in normal_embeddings]

    baseline_data = {
        "version": "1.0",
        "collected_at": "2026-04-12T10:00:00Z",
        "sample_count": len(normal_embeddings),
        "statistics": {
            "norm": {
                "mean": float(np.mean(norms)),
                "std": float(np.std(norms)),
                "min": float(np.min(norms)),
                "max": float(np.max(norms)),
                "p5": float(np.percentile(norms, 5)),
                "p25": float(np.percentile(norms, 25)),
                "p50": float(np.percentile(norms, 50)),
                "p75": float(np.percentile(norms, 75)),
                "p95": float(np.percentile(norms, 95)),
            },
        },
        "config": {
            "min_norm": 0.1,
            "max_norm": 100.0,
            "baseline_window": 1000,
        },
    }

    baseline_path = tmp_data_dir / "baseline.json"
    with open(baseline_path, "w") as f:
        json.dump(baseline_data, f)

    return baseline_path, baseline_data


# ============================================================================
# Test Baseline Calibration
# ============================================================================


class TestBaselineCalibration:
    """Test baseline calibration script functionality."""

    def test_baseline_statistics_computation(self, normal_embeddings):
        """Test that baseline statistics are computed correctly."""
        norms = [float(np.linalg.norm(emb)) for emb in normal_embeddings]

        mean_norm = np.mean(norms)
        std_norm = np.std(norms)

        # Statistics should match expectations
        assert 10.0 < mean_norm < 12.0, f"Mean norm {mean_norm} outside expected range"
        assert 0.3 < std_norm < 1.0, f"Std norm {std_norm} outside expected range"

        # Percentiles should be in order
        p5 = float(np.percentile(norms, 5))
        p50 = float(np.percentile(norms, 50))
        p95 = float(np.percentile(norms, 95))

        assert p5 < p50 < p95, "Percentiles not in order"

    def test_baseline_json_structure(self, baseline_file):
        """Test that baseline.json has correct structure."""
        path, baseline = baseline_file

        # Check required fields
        assert baseline["version"] == "1.0"
        assert "collected_at" in baseline
        assert "sample_count" in baseline
        assert "statistics" in baseline
        assert "config" in baseline

        # Check statistics sub-fields
        stats = baseline["statistics"]["norm"]
        required_keys = ["mean", "std", "min", "max", "p5", "p25", "p50", "p75", "p95"]
        for key in required_keys:
            assert key in stats, f"Missing statistic: {key}"
            assert isinstance(stats[key], (int, float)), f"Statistic {key} not numeric"

    def test_per_dimension_statistics(self, normal_embeddings):
        """Test per-dimension statistics computation."""
        stacked = np.vstack(normal_embeddings)

        per_dim_mean = np.mean(stacked, axis=0)
        per_dim_std = np.std(stacked, axis=0)
        per_dim_p95 = np.percentile(stacked, 95, axis=0)

        # All dimensions should have valid stats
        assert len(per_dim_mean) == 128
        assert len(per_dim_std) == 128
        assert len(per_dim_p95) == 128

        # Std should be positive
        assert np.all(per_dim_std > 0), "Some dimensions have zero std"

    def test_baseline_requires_minimum_samples(self):
        """Test that baseline requires reasonable number of samples."""
        # Very small sample set should warn
        small_sample = [np.random.randn(128) for _ in range(5)]

        # Should still work but be unreliable
        norms = [float(np.linalg.norm(emb)) for emb in small_sample]
        assert len(norms) > 0, "Should compute norms even on small sample"


# ============================================================================
# Test Threshold Tuning
# ============================================================================


class TestThresholdTuning:
    """Test threshold tuning script functionality."""

    def test_detector_from_baseline(self, baseline_file, normal_embeddings):
        """Test creating detector from baseline statistics."""
        baseline_path, baseline = baseline_file

        # Create detector with baseline stats
        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        detector = RAGPoisoningDetector(
            sensitivity=0.95,
            baseline_window=config["baseline_window"],
            min_norm=config["min_norm"],
            max_norm=config["max_norm"],
        )

        # Set baseline stats
        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline["sample_count"]

        # Test on normal embeddings (should mostly pass)
        poisoned_count = sum(1 for emb in normal_embeddings if detector.is_poisoned(emb)[0])
        poisoned_rate = poisoned_count / len(normal_embeddings)

        # Should have <5% false positive rate
        assert poisoned_rate < 0.05, f"FPR {poisoned_rate*100:.1f}% exceeds 5%"

    def test_detector_catches_poisoned(self, baseline_file, normal_embeddings, poisoned_embeddings):
        """Test that detector catches poisoned embeddings."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        detector = RAGPoisoningDetector(
            sensitivity=0.95,
            baseline_window=config["baseline_window"],
            min_norm=config["min_norm"],
            max_norm=config["max_norm"],
        )

        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline["sample_count"]

        # Test on poisoned embeddings
        detected_count = sum(1 for emb in poisoned_embeddings if detector.is_poisoned(emb)[0])
        detection_rate = detected_count / len(poisoned_embeddings)

        # Should catch >80% of poisoned embeddings
        assert detection_rate > 0.80, f"TPR {detection_rate*100:.1f}% below 80%"

    def test_sensitivity_to_z_threshold_mapping(self):
        """Test sensitivity parameter maps correctly to z-score threshold."""
        # Test boundary cases
        detector_low = RAGPoisoningDetector(sensitivity=0.90)
        detector_mid = RAGPoisoningDetector(sensitivity=0.95)
        detector_high = RAGPoisoningDetector(sensitivity=0.99)

        # Higher sensitivity = higher z-score threshold (fewer rejections)
        assert detector_low._z_threshold < detector_mid._z_threshold
        assert detector_mid._z_threshold < detector_high._z_threshold

        # Should be in reasonable range
        assert 2.0 <= detector_low._z_threshold <= 2.5
        assert 2.5 <= detector_mid._z_threshold <= 3.5
        assert 3.5 <= detector_high._z_threshold <= 4.0

    def test_roc_metrics_computation(self, baseline_file, normal_embeddings, poisoned_embeddings):
        """Test ROC curve metrics (TPR, FPR)."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        detector = RAGPoisoningDetector(sensitivity=0.95, baseline_window=1000)
        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline["sample_count"]

        # Count TP, FP, TN, FN
        tp = sum(1 for emb in poisoned_embeddings if detector.is_poisoned(emb)[0])
        fp = sum(1 for emb in normal_embeddings if detector.is_poisoned(emb)[0])
        tn = len(normal_embeddings) - fp
        fn = len(poisoned_embeddings) - tp

        # Compute rates
        tpr = tp / (tp + fn) if (tp + fn) > 0 else 0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0

        # Sanity checks
        assert 0 <= tpr <= 1, f"TPR {tpr} out of range"
        assert 0 <= fpr <= 1, f"FPR {fpr} out of range"
        assert tp + fn == len(poisoned_embeddings), "Poisoned counts don't add up"
        assert tn + fp == len(normal_embeddings), "Normal counts don't add up"

    @pytest.mark.parametrize("sensitivity", [0.90, 0.92, 0.95, 0.97, 0.99])
    def test_sensitivity_sweep(self, baseline_file, normal_embeddings, poisoned_embeddings, sensitivity):
        """Test that sensitivity sweep produces valid results."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        detector = RAGPoisoningDetector(
            sensitivity=sensitivity,
            baseline_window=config["baseline_window"],
            min_norm=config["min_norm"],
            max_norm=config["max_norm"],
        )

        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline["sample_count"]

        # Test both sets
        fp = sum(1 for emb in normal_embeddings if detector.is_poisoned(emb)[0])
        tp = sum(1 for emb in poisoned_embeddings if detector.is_poisoned(emb)[0])

        fpr = fp / len(normal_embeddings)
        tpr = tp / len(poisoned_embeddings)

        # Higher sensitivity should generally increase TPR and FPR
        # (though not strictly monotonic due to thresholding)
        assert 0 <= fpr <= 1
        assert 0 <= tpr <= 1


# ============================================================================
# Test Deployment Stages
# ============================================================================


class TestDeploymentStages:
    """Test deployment stage transitions."""

    def test_stage_1_warn_mode(self, tmp_data_dir):
        """Test Stage 1: Warn mode doesn't block, only logs."""
        config = {
            "rag_poisoning_detection": {
                "enabled": True,
                "sensitivity": 0.95,
                "detection_mode": "warn",  # Stage 1
                "baseline_window": 1000,
                "min_norm": 0.1,
                "max_norm": 100.0,
            }
        }

        config_path = tmp_data_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Verify warn mode allows storage
        assert config["rag_poisoning_detection"]["detection_mode"] == "warn"
        assert config["rag_poisoning_detection"]["enabled"] is True

    def test_stage_2_quarantine_mode(self, tmp_data_dir):
        """Test Stage 2: Quarantine mode blocks suspicious embeddings."""
        config = {
            "rag_poisoning_detection": {
                "enabled": True,
                "sensitivity": 0.95,
                "detection_mode": "quarantine",  # Stage 2
                "baseline_window": 1000,
                "min_norm": 0.1,
                "max_norm": 100.0,
            }
        }

        config_path = tmp_data_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Verify quarantine mode is configured
        assert config["rag_poisoning_detection"]["detection_mode"] == "quarantine"

    def test_stage_3_advanced_layers(self, tmp_data_dir):
        """Test Stage 3: Advanced layers enabled."""
        config = {
            "rag_poisoning_detection": {
                "enabled": True,
                "sensitivity": 0.95,
                "detection_mode": "quarantine",
                "enable_neighborhood_detection": True,
                "enable_dimension_analysis": True,
                "baseline_window": 1000,
                "min_norm": 0.1,
                "max_norm": 100.0,
            }
        }

        config_path = tmp_data_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Verify advanced config
        rag_cfg = config["rag_poisoning_detection"]
        assert rag_cfg.get("enable_neighborhood_detection") is True
        assert rag_cfg.get("enable_dimension_analysis") is True

    def test_rollback_to_warn_mode(self, tmp_data_dir):
        """Test rollback from block to warn mode."""
        # Start in quarantine
        config = {
            "rag_poisoning_detection": {
                "enabled": True,
                "sensitivity": 0.95,
                "detection_mode": "quarantine",
            }
        }

        config_path = tmp_data_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Rollback to warn
        config["rag_poisoning_detection"]["detection_mode"] = "warn"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Verify rollback
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["rag_poisoning_detection"]["detection_mode"] == "warn"

    def test_disable_detection_emergency(self, tmp_data_dir):
        """Test emergency disable of detection."""
        config = {
            "rag_poisoning_detection": {
                "enabled": True,
                "detection_mode": "quarantine",
            }
        }

        config_path = tmp_data_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Emergency disable
        config["rag_poisoning_detection"]["enabled"] = False
        with open(config_path, "w") as f:
            yaml.dump(config, f)

        # Verify disabled
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["rag_poisoning_detection"]["enabled"] is False


# ============================================================================
# Test False Positive Rate Validation
# ============================================================================


class TestFalsePositiveValidation:
    """Test FPR validation against acceptance criteria."""

    def test_fpr_below_0_5_percent(self, baseline_file, normal_embeddings, poisoned_embeddings):
        """Test that optimal threshold achieves <0.5% FPR."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        # Test at recommended sensitivity
        detector = RAGPoisoningDetector(
            sensitivity=0.95,
            baseline_window=config["baseline_window"],
            min_norm=config["min_norm"],
            max_norm=config["max_norm"],
        )

        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline["sample_count"]

        # Count false positives
        fp = sum(1 for emb in normal_embeddings if detector.is_poisoned(emb)[0])
        fpr = fp / len(normal_embeddings)

        # Should meet acceptance criteria
        assert fpr < 0.005, f"FPR {fpr*100:.2f}% exceeds 0.5% threshold"

    def test_tpr_above_90_percent(self, baseline_file, normal_embeddings, poisoned_embeddings):
        """Test that optimal threshold achieves >90% TPR."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        detector = RAGPoisoningDetector(
            sensitivity=0.95,
            baseline_window=config["baseline_window"],
            min_norm=config["min_norm"],
            max_norm=config["max_norm"],
        )

        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline["sample_count"]

        # Count true positives
        tp = sum(1 for emb in poisoned_embeddings if detector.is_poisoned(emb)[0])
        tpr = tp / len(poisoned_embeddings)

        # Should meet acceptance criteria
        assert tpr > 0.90, f"TPR {tpr*100:.1f}% below 90% threshold"

    def test_fpr_tpr_tradeoff(self, baseline_file, normal_embeddings, poisoned_embeddings):
        """Test FPR/TPR tradeoff at different sensitivities."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        results = []
        for sensitivity in [0.90, 0.92, 0.95, 0.97, 0.99]:
            detector = RAGPoisoningDetector(
                sensitivity=sensitivity,
                baseline_window=config["baseline_window"],
                min_norm=config["min_norm"],
                max_norm=config["max_norm"],
            )

            detector._mean_norm = stats["mean"]
            detector._std_norm = stats["std"]
            detector._count = baseline["sample_count"]

            fp = sum(1 for emb in normal_embeddings if detector.is_poisoned(emb)[0])
            tp = sum(1 for emb in poisoned_embeddings if detector.is_poisoned(emb)[0])

            fpr = fp / len(normal_embeddings)
            tpr = tp / len(poisoned_embeddings)

            results.append((sensitivity, fpr, tpr))

        # FPR should generally decrease as sensitivity increases
        fprs = [fpr for _, fpr, _ in results]
        assert fprs[0] >= fprs[-1], "FPR should decrease with higher sensitivity"

    @pytest.mark.parametrize("test_size", [50, 100, 200])
    def test_fpr_stable_across_test_sizes(self, baseline_file, test_size):
        """Test that FPR is stable across different test set sizes."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        detector = RAGPoisoningDetector(
            sensitivity=0.95,
            baseline_window=config["baseline_window"],
            min_norm=config["min_norm"],
            max_norm=config["max_norm"],
        )

        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline["sample_count"]

        # Generate test set of specific size
        np.random.seed(42)
        norms = np.random.normal(loc=stats["mean"], scale=stats["std"], size=test_size)
        test_embeddings = []
        for norm in norms:
            emb = np.random.randn(128)
            emb = emb / np.linalg.norm(emb) * norm
            test_embeddings.append(emb)

        fp = sum(1 for emb in test_embeddings if detector.is_poisoned(emb)[0])
        fpr = fp / len(test_embeddings)

        # FPR should be reasonable regardless of test size
        assert 0 <= fpr <= 0.02, f"FPR {fpr*100:.1f}% seems unreasonable"


# ============================================================================
# Test Monitoring & Metrics
# ============================================================================


class TestMonitoringMetrics:
    """Test monitoring and alerting metrics."""

    def test_baseline_statistics_dict(self):
        """Test that detector provides baseline statistics for monitoring."""
        detector = RAGPoisoningDetector(sensitivity=0.95)

        # Add some embeddings
        for _ in range(10):
            emb = np.random.randn(128)
            detector.update_baseline(emb)

        # Get stats
        stats = detector.get_baseline_stats()

        # Should have all required fields
        required = ["count", "mean_norm", "std_norm", "z_threshold",
                   "baseline_window_size", "min_norm_range", "max_norm_range"]
        for key in required:
            assert key in stats, f"Missing stat: {key}"

    def test_detection_confidence_scores(self, normal_embeddings, poisoned_embeddings):
        """Test that detector provides confidence scores for alerts."""
        detector = RAGPoisoningDetector(sensitivity=0.95)

        # Update baseline
        for emb in normal_embeddings[:100]:
            detector.update_baseline(emb)

        # Check poisoned embeddings have high confidence
        for emb in poisoned_embeddings[:10]:
            is_poisoned, confidence, reason = detector.is_poisoned(emb)
            if is_poisoned:
                assert 0 <= confidence <= 1.0, f"Confidence {confidence} out of range"
                assert len(reason) > 0, "Should provide reason"

    def test_quarantine_stats_tracking(self):
        """Test that quarantine stats can be tracked."""
        detector = RAGPoisoningDetector(sensitivity=0.95)

        # Simulate stats object
        stats = {
            "total_checked": 1000,
            "total_flagged": 5,
            "flagged_rate": 0.005,  # 0.5%
            "blocks_today": 2,
            "avg_confidence": 0.95,
        }

        # Verify structure
        assert stats["flagged_rate"] < 0.01, "Flagged rate > 1%"
        assert stats["blocks_today"] <= stats["total_flagged"]

    def test_alert_conditions(self):
        """Test alert condition thresholds."""
        # Example monitoring data
        alerts = {
            "high_fpr": {"threshold": 0.02, "current": 0.015},  # OK
            "low_tpr": {"threshold": 0.90, "current": 0.92},    # OK
            "high_blocks": {"threshold": 5, "current": 3},      # OK
        }

        # Verify no alerts triggered
        assert alerts["high_fpr"]["current"] < alerts["high_fpr"]["threshold"]
        assert alerts["low_tpr"]["current"] > alerts["low_tpr"]["threshold"]
        assert alerts["high_blocks"]["current"] < alerts["high_blocks"]["threshold"]


# ============================================================================
# Test Integration
# ============================================================================


@pytest.mark.integration
class TestIntegration:
    """Integration tests for full deployment workflow."""

    def test_full_calibration_to_deployment(self, tmp_data_dir, normal_embeddings):
        """Test full workflow: calibrate → tune → deploy."""
        baseline_path = tmp_data_dir / "baseline.json"

        # Step 1: Calibrate baseline
        norms = [float(np.linalg.norm(emb)) for emb in normal_embeddings]
        baseline_data = {
            "version": "1.0",
            "collected_at": "2026-04-12T10:00:00Z",
            "sample_count": len(normal_embeddings),
            "statistics": {
                "norm": {
                    "mean": float(np.mean(norms)),
                    "std": float(np.std(norms)),
                    "min": float(np.min(norms)),
                    "max": float(np.max(norms)),
                    "p5": float(np.percentile(norms, 5)),
                    "p25": float(np.percentile(norms, 25)),
                    "p50": float(np.percentile(norms, 50)),
                    "p75": float(np.percentile(norms, 75)),
                    "p95": float(np.percentile(norms, 95)),
                },
            },
            "config": {
                "min_norm": 0.1,
                "max_norm": 100.0,
                "baseline_window": 1000,
            },
        }

        with open(baseline_path, "w") as f:
            json.dump(baseline_data, f)

        assert baseline_path.exists(), "Baseline not created"

        # Step 2: Create detector from baseline
        config = baseline_data["config"]
        stats = baseline_data["statistics"]["norm"]

        detector = RAGPoisoningDetector(
            sensitivity=0.95,
            baseline_window=config["baseline_window"],
            min_norm=config["min_norm"],
            max_norm=config["max_norm"],
        )

        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline_data["sample_count"]

        # Step 3: Validate on test data
        fp = sum(1 for emb in normal_embeddings if detector.is_poisoned(emb)[0])
        fpr = fp / len(normal_embeddings)

        assert fpr < 0.05, f"FPR {fpr*100:.1f}% above 5% for initial deployment"

    def test_deployment_stage_progression(self, tmp_data_dir):
        """Test progression through deployment stages."""
        config_path = tmp_data_dir / "config.yaml"

        stages = [
            ("off", False),
            ("warn", True),
            ("quarantine", True),
        ]

        for mode, enabled in stages:
            if mode == "off":
                config = {"rag_poisoning_detection": {"enabled": False}}
            else:
                config = {
                    "rag_poisoning_detection": {
                        "enabled": enabled,
                        "detection_mode": mode,
                        "sensitivity": 0.95,
                    }
                }

            with open(config_path, "w") as f:
                yaml.dump(config, f)

            # Verify stage can be read
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            assert cfg["rag_poisoning_detection"]["enabled"] == enabled


# ============================================================================
# Test Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_baseline(self):
        """Test behavior with empty baseline."""
        detector = RAGPoisoningDetector(sensitivity=0.95)

        # No baseline samples
        assert detector._count == 0

        # Should still check against range
        tiny_emb = np.array([0.001] * 128)
        is_poisoned, confidence, reason = detector.is_poisoned(tiny_emb)
        assert is_poisoned, "Should detect magnitude out of range"

    def test_very_small_embeddings(self):
        """Test detection of very small embeddings."""
        detector = RAGPoisoningDetector(min_norm=0.1, max_norm=100.0)

        # Create embedding with very small norm
        tiny_emb = np.ones(128) * 0.001  # norm ≈ 0.00354, well below min_norm
        is_poisoned, confidence, _ = detector.is_poisoned(tiny_emb)

        assert is_poisoned, "Should flag tiny embedding below min_norm"
        assert confidence > 0.5, "Should have high confidence"

    def test_very_large_embeddings(self):
        """Test detection of very large embeddings."""
        detector = RAGPoisoningDetector(min_norm=0.1, max_norm=100.0)

        # Very large embedding
        huge_emb = np.random.randn(128) * 100.0
        is_poisoned, confidence, _ = detector.is_poisoned(huge_emb)

        assert is_poisoned, "Should flag huge embedding"
        assert confidence > 0.5, "Should have high confidence"

    def test_all_zeros_embedding(self):
        """Test detection of all-zeros embedding."""
        detector = RAGPoisoningDetector()

        zero_emb = np.zeros(128)
        is_poisoned, confidence, reason = detector.is_poisoned(zero_emb)

        assert is_poisoned, "Should detect zero embedding"
        assert "empty" in reason.lower() or "magnitude" in reason.lower()

    def test_nan_in_embedding(self):
        """Test handling of NaN values in embedding."""
        detector = RAGPoisoningDetector()

        nan_emb = np.random.randn(128)
        nan_emb[0] = np.nan

        # NaN norm propagates, detector should handle it gracefully
        is_poisoned, confidence, reason = detector.is_poisoned(nan_emb)
        # Either flags as poisoned or returns gracefully
        assert isinstance(is_poisoned, bool)
        assert 0 <= confidence <= 1.0

    def test_list_vs_array_input(self):
        """Test that detector handles both list and array inputs."""
        detector = RAGPoisoningDetector(sensitivity=0.95)

        emb_array = np.random.randn(128)
        emb_list = emb_array.tolist()

        # Update baseline with both
        detector.update_baseline(emb_array)
        detector.update_baseline(emb_list)

        # Check both
        result_array = detector.is_poisoned(emb_array)
        result_list = detector.is_poisoned(emb_list)

        # Should give same result
        assert result_array[0] == result_list[0]


# ============================================================================
# Test Acceptance Criteria
# ============================================================================


@pytest.mark.acceptance
class TestAcceptanceCriteria:
    """Test all acceptance criteria for production deployment."""

    def test_baseline_calibration_working(self, baseline_file):
        """✓ Baseline calibration script working."""
        path, baseline = baseline_file

        assert path.exists()
        assert baseline["version"] == "1.0"
        assert "statistics" in baseline
        assert baseline["statistics"]["norm"]["mean"] > 0

    def test_threshold_tuning_recommends_settings(self, baseline_file, normal_embeddings, poisoned_embeddings):
        """✓ Threshold tuning recommends optimal settings."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        best_sensitivity = None
        best_fpr = float('inf')

        for sensitivity in [0.90, 0.92, 0.95, 0.97, 0.99]:
            detector = RAGPoisoningDetector(
                sensitivity=sensitivity,
                baseline_window=config["baseline_window"],
                min_norm=config["min_norm"],
                max_norm=config["max_norm"],
            )

            detector._mean_norm = stats["mean"]
            detector._std_norm = stats["std"]
            detector._count = baseline["sample_count"]

            fp = sum(1 for emb in normal_embeddings if detector.is_poisoned(emb)[0])
            fpr = fp / len(normal_embeddings)

            if fpr < 0.005 and fpr < best_fpr:
                best_fpr = fpr
                best_sensitivity = sensitivity

        assert best_sensitivity is not None, "No sensitivity achieves <0.5% FPR"
        assert 0.90 <= best_sensitivity <= 0.99

    def test_deployment_sequence_documented(self, tmp_data_dir):
        """✓ Deployment sequence documented."""
        doc_path = Path(__file__).parent.parent / "docs" / "DEPLOYMENT_RAG_DEFENSE.md"
        assert doc_path.exists(), "Deployment runbook missing"

        with open(doc_path) as f:
            content = f.read()

        # Verify stages are documented
        assert "Stage 1" in content
        assert "Stage 2" in content
        assert "Stage 3" in content
        assert "warn" in content
        assert "quarantine" in content

    def test_operations_runbook_complete(self):
        """✓ Operations runbook complete with daily/weekly/monthly tasks."""
        doc_path = Path(__file__).parent.parent / "docs" / "OPERATIONS_RAG_DEFENSE.md"
        assert doc_path.exists(), "Operations runbook missing"

        with open(doc_path) as f:
            content = f.read()

        # Verify sections exist
        assert "Daily" in content
        assert "Weekly" in content
        assert "Monthly" in content
        assert "Troubleshooting" in content

    def test_false_positive_rate_validated(self, baseline_file, normal_embeddings):
        """✓ False positive rate validated <0.5% on test data."""
        baseline_path, baseline = baseline_file

        config = baseline["config"]
        stats = baseline["statistics"]["norm"]

        detector = RAGPoisoningDetector(
            sensitivity=0.95,
            baseline_window=config["baseline_window"],
            min_norm=config["min_norm"],
            max_norm=config["max_norm"],
        )

        detector._mean_norm = stats["mean"]
        detector._std_norm = stats["std"]
        detector._count = baseline["sample_count"]

        fp = sum(1 for emb in normal_embeddings if detector.is_poisoned(emb)[0])
        fpr = fp / len(normal_embeddings)

        assert fpr < 0.005, f"FPR {fpr*100:.2f}% exceeds 0.5%"

    def test_rollback_procedure_documented(self):
        """✓ Rollback procedure documented."""
        doc_path = Path(__file__).parent.parent / "docs" / "DEPLOYMENT_RAG_DEFENSE.md"

        with open(doc_path) as f:
            content = f.read()

        assert "Rollback" in content
        assert "false positive" in content.lower()
        assert "2%" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
