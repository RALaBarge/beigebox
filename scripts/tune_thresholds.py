#!/usr/bin/env python3
"""
Threshold Tuning Tool for RAG Poisoning Detection.

Evaluates different sensitivity thresholds on a mix of legitimate and poisoned
embeddings to find the optimal operating point.

Usage:
    python scripts/tune_thresholds.py \\
        --baseline baseline.json \\
        --test-legit test_legit_embeddings.json \\
        --test-poison test_poison_embeddings.json \\
        --output recommended_config.yaml

Output includes:
    - Sensitivity range tests (0.90 to 0.99)
    - False positive rate (FPR) on legitimate
    - True positive rate (TPR) on poisoned
    - ROC curve data
    - Recommended threshold for <0.5% FPR
"""

import argparse
import json
import logging
import sys
import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import numpy as np
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@dataclass
class ThresholdMetrics:
    """Metrics for a single sensitivity threshold."""
    sensitivity: float
    z_threshold: float
    tp_count: int
    fp_count: int
    tn_count: int
    fn_count: int

    @property
    def tpr(self) -> float:
        """True positive rate (catch poisoned)."""
        denom = self.tp_count + self.fn_count
        return self.tp_count / denom if denom > 0 else 0.0

    @property
    def fpr(self) -> float:
        """False positive rate (reject legitimate)."""
        denom = self.fp_count + self.tn_count
        return self.fp_count / denom if denom > 0 else 0.0

    @property
    def accuracy(self) -> float:
        """Overall accuracy."""
        denom = self.tp_count + self.tn_count + self.fp_count + self.fn_count
        return (self.tp_count + self.tn_count) / denom if denom > 0 else 0.0

    @property
    def f1_score(self) -> float:
        """F1 score (harmonic mean of precision and recall)."""
        if self.tp_count == 0:
            return 0.0
        precision = self.tp_count / (self.tp_count + self.fp_count) if (self.tp_count + self.fp_count) > 0 else 0.0
        recall = self.tpr
        denom = precision + recall
        return 2 * (precision * recall) / denom if denom > 0 else 0.0


def load_baseline(baseline_path: str) -> dict:
    """Load baseline statistics from JSON file."""
    logger.info("Loading baseline from %s", baseline_path)
    try:
        with open(baseline_path, "r") as f:
            baseline = json.load(f)
        logger.info("Baseline loaded: %d samples", baseline.get("sample_count", 0))
        return baseline
    except Exception as e:
        logger.error("Failed to load baseline: %s", e)
        sys.exit(1)


def load_embeddings(path: str, label: str) -> list[np.ndarray]:
    """Load embeddings from JSON file."""
    logger.info("Loading %s embeddings from %s", label, path)
    try:
        with open(path, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                embeddings = data
            elif isinstance(data, dict) and "embeddings" in data:
                embeddings = data["embeddings"]
            else:
                embeddings = []
        result = [np.array(emb, dtype=np.float32) for emb in embeddings]
        logger.info("Loaded %d %s embeddings", len(result), label)
        return result
    except Exception as e:
        logger.error("Failed to load embeddings from %s: %s", path, e)
        sys.exit(1)


def create_detector_from_baseline(baseline: dict, sensitivity: float) -> RAGPoisoningDetector:
    """Create a detector initialized with baseline statistics."""
    config = baseline.get("config", {})
    detector = RAGPoisoningDetector(
        sensitivity=sensitivity,
        baseline_window=config.get("baseline_window", 1000),
        min_norm=config.get("min_norm", 0.1),
        max_norm=config.get("max_norm", 100.0),
    )

    # Manually set baseline statistics from calibration
    stats = baseline.get("statistics", {}).get("norm", {})
    if stats:
        # Initialize with baseline values
        detector._mean_norm = stats.get("mean", 1.0)
        detector._std_norm = stats.get("std", 1.0)
        detector._count = baseline.get("sample_count", 1000)

        logger.debug(
            "Detector initialized: mean_norm=%.4f, std_norm=%.4f, z_threshold=%.2f",
            detector._mean_norm,
            detector._std_norm,
            detector._z_threshold,
        )

    return detector


def evaluate_threshold(
    detector: RAGPoisoningDetector,
    legit_embeddings: list[np.ndarray],
    poison_embeddings: list[np.ndarray],
) -> ThresholdMetrics:
    """Evaluate detector on test set."""
    tp_count = 0  # poisoned detected as poisoned
    fp_count = 0  # legit detected as poisoned
    tn_count = 0  # legit detected as legit
    fn_count = 0  # poisoned detected as legit

    # Test legitimate embeddings (should all pass)
    for emb in legit_embeddings:
        is_poisoned, _, _ = detector.is_poisoned(emb)
        if is_poisoned:
            fp_count += 1
        else:
            tn_count += 1

    # Test poisoned embeddings (should all fail)
    for emb in poison_embeddings:
        is_poisoned, _, _ = detector.is_poisoned(emb)
        if is_poisoned:
            tp_count += 1
        else:
            fn_count += 1

    return ThresholdMetrics(
        sensitivity=detector.sensitivity,
        z_threshold=detector._z_threshold,
        tp_count=tp_count,
        fp_count=fp_count,
        tn_count=tn_count,
        fn_count=fn_count,
    )


def find_optimal_threshold(
    all_metrics: list[ThresholdMetrics],
    target_fpr: float = 0.005,  # <0.5%
) -> Optional[ThresholdMetrics]:
    """
    Find optimal threshold that achieves target FPR.

    Prefers higher TPR when FPR constraint is met.

    Args:
        all_metrics: List of metrics for all tested thresholds
        target_fpr: Target false positive rate (default: <0.5%)

    Returns:
        Best ThresholdMetrics or None if target unachievable
    """
    # Filter to those meeting FPR target
    candidates = [m for m in all_metrics if m.fpr <= target_fpr]

    if not candidates:
        logger.warning(
            "No threshold achieves target FPR of %.2f%%",
            target_fpr * 100
        )
        # Return the one with lowest FPR
        return min(all_metrics, key=lambda m: m.fpr)

    # Among candidates, prefer highest TPR
    return max(candidates, key=lambda m: m.tpr)


def save_recommended_config(
    optimal: ThresholdMetrics,
    baseline: dict,
    output_path: str,
) -> None:
    """Save recommended configuration to YAML file."""
    config = baseline.get("config", {})

    # Build recommended config structure
    recommended = {
        "description": "Auto-tuned RAG defense configuration",
        "tuning": {
            "date": baseline.get("collected_at"),
            "test_set_size": "See test data",
            "optimal_sensitivity": optimal.sensitivity,
            "optimal_z_threshold": optimal.z_threshold,
            "achieved_fpr": f"{optimal.fpr * 100:.2f}%",
            "achieved_tpr": f"{optimal.tpr * 100:.2f}%",
        },
        "rag_poisoning_detection": {
            "enabled": True,
            "sensitivity": optimal.sensitivity,
            "detection_mode": "warn",  # Stage 1: warn only
            "baseline_window": config.get("baseline_window", 1000),
            "min_norm": config.get("min_norm", 0.1),
            "max_norm": config.get("max_norm", 100.0),
        },
    }

    logger.info("Writing recommended config to %s", output_path)
    try:
        with open(output_path, "w") as f:
            yaml.dump(recommended, f, default_flow_style=False)
        logger.info("Recommended config saved")
    except Exception as e:
        logger.error("Failed to write recommended config: %s", e)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Tune RAG poisoning detection thresholds"
    )
    parser.add_argument(
        "--baseline",
        type=str,
        required=True,
        help="Path to baseline.json (from calibrate_embedding_baseline.py)",
    )
    parser.add_argument(
        "--test-legit",
        type=str,
        required=True,
        help="Path to JSON file with legitimate test embeddings",
    )
    parser.add_argument(
        "--test-poison",
        type=str,
        required=True,
        help="Path to JSON file with poisoned test embeddings",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="recommended_config.yaml",
        help="Output YAML file for recommended configuration",
    )
    parser.add_argument(
        "--target-fpr",
        type=float,
        default=0.005,
        help="Target false positive rate (default: 0.005 = 0.5%%)",
    )
    parser.add_argument(
        "--sensitivity-min",
        type=float,
        default=0.90,
        help="Minimum sensitivity to test (default: 0.90)",
    )
    parser.add_argument(
        "--sensitivity-max",
        type=float,
        default=0.99,
        help="Maximum sensitivity to test (default: 0.99)",
    )
    parser.add_argument(
        "--sensitivity-step",
        type=float,
        default=0.01,
        help="Sensitivity step size (default: 0.01)",
    )

    args = parser.parse_args()

    # Load data
    baseline = load_baseline(args.baseline)
    legit_embeddings = load_embeddings(args.test_legit, "legitimate")
    poison_embeddings = load_embeddings(args.test_poison, "poisoned")

    if not legit_embeddings or not poison_embeddings:
        logger.error("Test sets must be non-empty")
        sys.exit(1)

    logger.info("Test set: %d legitimate, %d poisoned", len(legit_embeddings), len(poison_embeddings))

    # Test sensitivity range
    sensitivities = np.arange(args.sensitivity_min, args.sensitivity_max + args.sensitivity_step / 2, args.sensitivity_step)
    logger.info("Testing %d sensitivity values from %.2f to %.2f", len(sensitivities), sensitivities[0], sensitivities[-1])

    all_metrics = []
    for sensitivity in sensitivities:
        detector = create_detector_from_baseline(baseline, sensitivity)
        metrics = evaluate_threshold(detector, legit_embeddings, poison_embeddings)
        all_metrics.append(metrics)
        logger.debug(
            "Sensitivity %.2f: TPR=%.1f%% FPR=%.1f%% Accuracy=%.1f%%",
            sensitivity,
            metrics.tpr * 100,
            metrics.fpr * 100,
            metrics.accuracy * 100,
        )

    # Find optimal
    optimal = find_optimal_threshold(all_metrics, target_fpr=args.target_fpr)

    # Print results table
    print("\n" + "="*80)
    print("THRESHOLD TUNING RESULTS")
    print("="*80)
    print(f"Test set: {len(legit_embeddings)} legitimate, {len(poison_embeddings)} poisoned")
    print(f"Target FPR: {args.target_fpr*100:.2f}%")
    print("\n{:12s} {:10s} {:8s} {:8s} {:12s} {:12s} {:10s}".format(
        "Sensitivity", "Z-Thresh", "TPR%", "FPR%", "Accuracy%", "F1 Score", "Status"
    ))
    print("-" * 80)

    for metrics in all_metrics:
        status = "OPTIMAL" if metrics.sensitivity == optimal.sensitivity else ""
        print("{:12.2f} {:10.2f} {:8.1f} {:8.1f} {:12.1f} {:12.4f} {:10s}".format(
            metrics.sensitivity,
            metrics.z_threshold,
            metrics.tpr * 100,
            metrics.fpr * 100,
            metrics.accuracy * 100,
            metrics.f1_score,
            status,
        ))

    print("\n" + "="*80)
    print("RECOMMENDED CONFIGURATION")
    print("="*80)
    print(f"Sensitivity: {optimal.sensitivity:.2f}")
    print(f"Z-Threshold: {optimal.z_threshold:.2f}")
    print(f"True Positive Rate: {optimal.tpr*100:.1f}%")
    print(f"False Positive Rate: {optimal.fpr*100:.2f}%")
    print(f"Accuracy: {optimal.accuracy*100:.1f}%")
    print(f"F1 Score: {optimal.f1_score:.4f}")
    print("\nDeployment Stage 1: 'warn' mode (3 days)")
    print("  - Log suspicious embeddings")
    print("  - Monitor FPR daily")
    print("  - Proceed to Stage 2 if FPR < 0.5%")
    print("="*80 + "\n")

    # Save recommended config
    save_recommended_config(optimal, baseline, args.output)


if __name__ == "__main__":
    main()
