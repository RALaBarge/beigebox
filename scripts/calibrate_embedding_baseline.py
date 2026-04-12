#!/usr/bin/env python3
"""
Baseline Calibration Tool for RAG Poisoning Detection.

Collects baseline embedding statistics from legitimate corpus for production deployment.
Analyzes N embeddings to compute mean, std, percentiles, and per-dimension stats.

Usage:
    python scripts/calibrate_embedding_baseline.py \\
        --chroma-path /path/to/chroma \\
        --output baseline.json \\
        --samples 200 \\
        --min-norm 0.1 \\
        --max-norm 100.0

Output (baseline.json):
    {
        "version": "1.0",
        "collected_at": "2026-04-12T10:00:00Z",
        "sample_count": 200,
        "statistics": {
            "norm": {
                "mean": 11.234,
                "std": 0.456,
                "min": 10.001,
                "max": 12.789,
                "p5": 10.500,
                "p25": 11.000,
                "p50": 11.250,
                "p75": 11.500,
                "p95": 11.900
            },
            "per_dimension": {
                "mean": [...],    # dimension-wise mean
                "std": [...],      # dimension-wise std
                "p95": [...]       # dimension-wise 95th percentile
            }
        },
        "config": {
            "min_norm": 0.1,
            "max_norm": 100.0,
            "baseline_window": 1000
        }
    }
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def get_embeddings_from_chroma(chroma_path: str, max_samples: int = 200) -> list[np.ndarray]:
    """
    Extract embeddings from ChromaDB collection.

    Args:
        chroma_path: Path to ChromaDB persistent storage
        max_samples: Maximum number of embeddings to retrieve

    Returns:
        List of embedding vectors as numpy arrays
    """
    try:
        import chromadb
    except ImportError:
        logger.error("chromadb not installed. Install with: pip install chromadb")
        sys.exit(1)

    chroma_path = Path(chroma_path)
    if not chroma_path.exists():
        logger.error("ChromaDB path does not exist: %s", chroma_path)
        sys.exit(1)

    logger.info("Connecting to ChromaDB at %s", chroma_path)

    try:
        client = chromadb.PersistentClient(path=str(chroma_path))
        collection = client.get_collection(name="conversations")
    except Exception as e:
        logger.error("Failed to connect to ChromaDB: %s", e)
        sys.exit(1)

    count = collection.count()
    logger.info("Collection contains %d embeddings total", count)

    if count == 0:
        logger.error("Collection is empty — cannot calibrate on zero embeddings")
        sys.exit(1)

    sample_size = min(max_samples, count)
    logger.info("Sampling %d embeddings for calibration", sample_size)

    embeddings = []
    try:
        # Get all embeddings with pagination to avoid memory issues
        batch_size = 100
        for offset in range(0, sample_size, batch_size):
            batch_count = min(batch_size, sample_size - offset)
            results = collection.get(
                limit=batch_count,
                offset=offset,
                include=["embeddings"]
            )

            if results and "embeddings" in results:
                batch_embeddings = results["embeddings"]
                embeddings.extend(batch_embeddings)
                logger.debug("Retrieved %d embeddings (offset=%d)", len(batch_embeddings), offset)

        logger.info("Successfully retrieved %d embeddings", len(embeddings))
    except Exception as e:
        logger.error("Failed to retrieve embeddings from ChromaDB: %s", e)
        sys.exit(1)

    if not embeddings:
        logger.error("No embeddings retrieved from ChromaDB")
        sys.exit(1)

    return [np.array(emb, dtype=np.float32) for emb in embeddings]


def compute_baseline_statistics(
    embeddings: list[np.ndarray],
    min_norm: float = 0.1,
    max_norm: float = 100.0,
) -> dict:
    """
    Compute comprehensive baseline statistics from embeddings.

    Args:
        embeddings: List of embedding vectors
        min_norm: Minimum safe L2 norm
        max_norm: Maximum safe L2 norm

    Returns:
        Dictionary of baseline statistics
    """
    logger.info("Computing statistics on %d embeddings", len(embeddings))

    # Compute L2 norms
    norms = np.array([float(np.linalg.norm(emb)) for emb in embeddings])

    # Filter outliers (beyond safe range) for reference statistics
    valid_norms = norms[(norms >= min_norm) & (norms <= max_norm)]

    logger.info(
        "Norms: %d valid (within [%.1f, %.1f]), %d outliers",
        len(valid_norms),
        min_norm,
        max_norm,
        len(norms) - len(valid_norms),
    )

    # Norm statistics
    norm_stats = {
        "mean": float(np.mean(norms)),
        "std": float(np.std(norms)),
        "min": float(np.min(norms)),
        "max": float(np.max(norms)),
        "p5": float(np.percentile(norms, 5)),
        "p25": float(np.percentile(norms, 25)),
        "p50": float(np.percentile(norms, 50)),
        "p75": float(np.percentile(norms, 75)),
        "p95": float(np.percentile(norms, 95)),
    }

    logger.info("Norm stats: mean=%.4f, std=%.4f, range=[%.4f, %.4f]",
                norm_stats["mean"], norm_stats["std"],
                norm_stats["min"], norm_stats["max"])

    # Per-dimension statistics (using valid embeddings only)
    if len(valid_norms) > 0:
        valid_embeddings = [
            embeddings[i]
            for i in range(len(embeddings))
            if norms[i] >= min_norm and norms[i] <= max_norm
        ]

        if valid_embeddings:
            stacked = np.vstack(valid_embeddings)
            per_dim_stats = {
                "mean": [float(x) for x in np.mean(stacked, axis=0)],
                "std": [float(x) for x in np.std(stacked, axis=0)],
                "p95": [float(x) for x in np.percentile(stacked, 95, axis=0)],
            }
        else:
            per_dim_stats = {"mean": [], "std": [], "p95": []}
    else:
        per_dim_stats = {"mean": [], "std": [], "p95": []}

    return {
        "norm": norm_stats,
        "per_dimension": per_dim_stats,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate RAG poisoning detector baseline from ChromaDB corpus"
    )
    parser.add_argument(
        "--chroma-path",
        type=str,
        default="./data/chroma",
        help="Path to ChromaDB persistent storage (default: ./data/chroma)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="baseline.json",
        help="Output JSON file for baseline statistics (default: baseline.json)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=200,
        help="Number of embeddings to sample for calibration (default: 200)",
    )
    parser.add_argument(
        "--min-norm",
        type=float,
        default=0.1,
        help="Minimum safe L2 norm (default: 0.1)",
    )
    parser.add_argument(
        "--max-norm",
        type=float,
        default=100.0,
        help="Maximum safe L2 norm (default: 100.0)",
    )
    parser.add_argument(
        "--baseline-window",
        type=int,
        default=1000,
        help="Baseline window size for detector (default: 1000)",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.samples < 50:
        logger.warning("Recommended minimum samples: 50 (got %d)", args.samples)

    if args.min_norm >= args.max_norm:
        logger.error("min_norm (%.1f) must be < max_norm (%.1f)", args.min_norm, args.max_norm)
        sys.exit(1)

    # Extract embeddings from ChromaDB
    embeddings = get_embeddings_from_chroma(args.chroma_path, max_samples=args.samples)

    # Compute baseline statistics
    stats = compute_baseline_statistics(
        embeddings,
        min_norm=args.min_norm,
        max_norm=args.max_norm,
    )

    # Create output structure
    baseline_data = {
        "version": "1.0",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(embeddings),
        "statistics": stats,
        "config": {
            "min_norm": args.min_norm,
            "max_norm": args.max_norm,
            "baseline_window": args.baseline_window,
        },
    }

    # Write to JSON file
    output_path = Path(args.output)
    try:
        with open(output_path, "w") as f:
            json.dump(baseline_data, f, indent=2)
        logger.info("Baseline calibration saved to %s", output_path)
    except Exception as e:
        logger.error("Failed to write baseline file: %s", e)
        sys.exit(1)

    # Print summary
    print("\n" + "="*60)
    print("BASELINE CALIBRATION SUMMARY")
    print("="*60)
    print(f"Samples analyzed: {baseline_data['sample_count']}")
    print(f"Collected: {baseline_data['collected_at']}")
    print(f"\nNorm Statistics:")
    for key, val in stats["norm"].items():
        print(f"  {key:8s}: {val:.6f}")
    print(f"\nSaved to: {output_path}")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
