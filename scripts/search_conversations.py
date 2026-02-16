#!/usr/bin/env python3
"""
Semantic search over stored conversations via ChromaDB.

Usage:
    python scripts/search_conversations.py "docker networking"
    python scripts/search_conversations.py "python async" --results 10
    python scripts/search_conversations.py "bug fix" --role user
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from middleware.config import get_config
from middleware.storage.vector_store import VectorStore


def main():
    parser = argparse.ArgumentParser(description="Semantic search over conversations")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--results", "-n", type=int, default=5, help="Number of results")
    parser.add_argument("--role", "-r", choices=["user", "assistant"], default=None,
                        help="Filter by message role")
    args = parser.parse_args()

    cfg = get_config()
    store = VectorStore(
        chroma_path=cfg["storage"]["chroma_path"],
        embedding_model=cfg["embedding"]["model"],
        embedding_url=cfg["embedding"]["backend_url"],
    )

    print(f"Searching for: '{args.query}' (top {args.results})")
    if args.role:
        print(f"Filtering by role: {args.role}")
    print("-" * 60)

    results = store.search(args.query, n_results=args.results, role_filter=args.role)

    if not results:
        print("No results found.")
        return

    for i, hit in enumerate(results, 1):
        meta = hit["metadata"]
        distance = hit["distance"]
        content = hit["content"]

        # Truncate long content for display
        if len(content) > 200:
            content = content[:200] + "..."

        print(f"\n[{i}] Score: {1 - distance:.3f} | Role: {meta.get('role', '?')} | Model: {meta.get('model', '?')}")
        print(f"    Conv: {meta.get('conversation_id', '?')[:12]}... | Time: {meta.get('timestamp', '?')}")
        print(f"    {content}")


if __name__ == "__main__":
    main()
