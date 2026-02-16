#!/usr/bin/env python3
"""
Export all conversations from SQLite to OpenAI-compatible JSON.
This is the portable format — import it into any tool that speaks OpenAI API.

Usage:
    python scripts/export_conversations.py --output my_conversations.json
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from middleware.config import get_config
from middleware.storage.sqlite_store import SQLiteStore


def main():
    parser = argparse.ArgumentParser(description="Export conversations to JSON")
    parser.add_argument("--output", "-o", default="conversations_export.json",
                        help="Output file path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    cfg = get_config()
    store = SQLiteStore(cfg["storage"]["sqlite_path"])
    stats = store.get_stats()

    print(f"Database: {cfg['storage']['sqlite_path']}")
    print(f"Conversations: {stats['conversations']}")
    print(f"Messages: {stats['messages']} (user: {stats['user_messages']}, assistant: {stats['assistant_messages']})")

    data = store.export_all_json()

    indent = 2 if args.pretty else None
    with open(args.output, "w") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)

    print(f"\nExported {len(data)} conversations to {args.output}")


if __name__ == "__main__":
    main()
