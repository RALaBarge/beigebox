"""
RAG Poisoning Detector Calibration Tool.

Helps operators calibrate the detector on an existing embedding corpus.
Runs baseline collection to establish statistics for anomaly detection.

Usage:
  python -m beigebox.tools.rag_calibration --chroma-path /path/to/chroma \
    --output stats.json
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector
from beigebox.storage.backends.chroma import ChromaBackend

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def calibrate_on_corpus(
    chroma_path: str,
    max_samples: int = 1000,
) -> RAGPoisoningDetector:
    """
    Calibrate detector on existing ChromaDB corpus.

    Extracts embeddings from the ChromaDB collection and builds baseline
    statistics. Returns a fully calibrated detector.

    Args:
        chroma_path: Path to ChromaDB persistent storage
        max_samples: Maximum number of vectors to analyze (for speed)

    Returns:
        Calibrated RAGPoisoningDetector instance
    """
    logger.info("Connecting to ChromaDB at %s", chroma_path)

    try:
        backend = ChromaBackend(chroma_path)
    except Exception as e:
        logger.error("Failed to connect to ChromaDB: %s", e)
        sys.exit(1)

    collection_count = backend.count()
    logger.info("Collection contains %d embeddings", collection_count)

    if collection_count == 0:
        logger.error("Collection is empty — cannot calibrate on zero embeddings")
        sys.exit(1)

    # Extract sample of embeddings
    # Note: ChromaDB doesn't have a direct "get all" method, so we sample via query
    logger.info("Extracting sample of %d embeddings for calibration", min(max_samples, collection_count))

    detector = RAGPoisoningDetector()

    # Create a dummy query embedding to fetch results (arbitrary)
    sample_query = np.random.randn(128).tolist()

    try:
        results = backend.query(sample_query, n_results=min(max_samples, collection_count))
    except Exception as e:
        logger.error("Failed to query ChromaDB: %s", e)
        sys.exit(1)

    if not results or "embeddings" not in results:
        logger.error("Failed to retrieve embeddings from ChromaDB")
        sys.exit(1)

    # ChromaDB returns results keyed by 0 (first result set)
    embeddings_list = results.get("embeddings", [[]])[0] if results.get("embeddings") else []

    if not embeddings_list:
        logger.error("No embeddings returned from query")
        sys.exit(1)

    logger.info("Processing %d embeddings", len(embeddings_list))

    # Update baseline with all sampled embeddings
    for i, emb in enumerate(embeddings_list):
        if i % 100 == 0:
            logger.debug("Processed %d embeddings", i)
        detector.update_baseline(np.array(emb))

    # Get final statistics
    stats = detector.get_baseline_stats()
    logger.info("Calibration complete:")
    logger.info("  Mean norm: %.4f", stats["mean_norm"])
    logger.info("  Std norm: %.4f", stats["std_norm"])
    logger.info("  Z-score threshold: %.2f", stats["z_threshold"])
    logger.info("  Samples: %d", stats["count"])

    return detector


def main():
    """Calibration CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Calibrate RAG poisoning detector on existing ChromaDB corpus"
    )
    parser.add_argument(
        "--chroma-path",
        required=True,
        help="Path to ChromaDB persistent storage",
    )
    parser.add_argument(
        "--output",
        default="rag_calibration_stats.json",
        help="Output file for calibration statistics (JSON)",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=1000,
        help="Maximum embeddings to sample for calibration",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Run calibration
    detector = calibrate_on_corpus(args.chroma_path, max_samples=args.max_samples)

    # Write statistics to file
    stats = detector.get_baseline_stats()
    output_path = Path(args.output)

    try:
        with open(output_path, "w") as f:
            json.dump(stats, f, indent=2)
        logger.info("Calibration statistics written to %s", output_path)
    except Exception as e:
        logger.error("Failed to write output file: %s", e)
        sys.exit(1)

    # Print summary
    print("\n" + "=" * 60)
    print("RAG POISONING DETECTOR CALIBRATION SUMMARY")
    print("=" * 60)
    print(f"Corpus size: {stats['count']} embeddings")
    print(f"Mean L2 norm: {stats['mean_norm']:.4f}")
    print(f"Std L2 norm: {stats['std_norm']:.4f}")
    print(f"Z-score threshold: {stats['z_threshold']:.2f}")
    print(f"Safe range: [{stats['min_norm_range']:.2f}, {stats['max_norm_range']:.2f}]")
    print(f"\nConfiguration to use:")
    print(f"  sensitivity: 0.95")
    print(f"  baseline_window: {stats['baseline_window_size']}")
    print(f"  min_norm: {stats['min_norm_range']:.1f}")
    print(f"  max_norm: {stats['max_norm_range']:.1f}")
    print(f"\nFull statistics written to: {output_path}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
