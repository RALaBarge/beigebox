#!/usr/bin/env python3
"""
Export all conversations from SQLite to OpenAI-compatible JSON.
This is the portable format — import it into any tool that speaks OpenAI API.

Usage:
    python scripts/export_conversations.py --output my_conversations.json
    python scripts/export_conversations.py --pretty --model qwen3:32b
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from beigebox.config import get_config
from beigebox.storage.sqlite_store import SQLiteStore


def main():
    parser = argparse.ArgumentParser(description="Export conversations to JSON")
    parser.add_argument("--output", "-o", default="conversations_export.json",
                        help="Output file path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--model", "-m", default=None,
                        help="Filter to conversations using this model")
    args = parser.parse_args()

    cfg = get_config()
    store = SQLiteStore(cfg["storage"]["sqlite_path"])
    stats = store.get_stats()

    print(f"Database: {cfg['storage']['sqlite_path']}")
    print(f"Conversations: {stats['conversations']}")
    print(f"Messages: {stats['messages']} (user: {stats['user_messages']}, assistant: {stats['assistant_messages']})")

    data = store.export_all_json()

    # Filter by model if specified
    if args.model:
        filtered = []
        for conv in data:
            msgs = [m for m in conv["messages"] if m.get("model") == args.model]
            if msgs:
                conv["messages"] = msgs
                filtered.append(conv)
        data = filtered
        print(f"Filtered to {len(data)} conversations with model '{args.model}'")

    indent = 2 if args.pretty else None
    with open(args.output, "w") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)

    print(f"\nExported {len(data)} conversations to {args.output}")


if __name__ == "__main__":
    main()
