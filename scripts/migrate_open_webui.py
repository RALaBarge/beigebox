#!/usr/bin/env python3
"""
Import existing Open WebUI conversation history into BeigeBox.
Reads from Open WebUI's SQLite DB and writes to our SQLite + ChromaDB.

Usage:
    python scripts/migrate_open_webui.py --source ~/.config/open-webui/webui.db
    python scripts/migrate_open_webui.py --source /path/to/webui.db --dry-run
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from beigebox.config import get_config
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.vector_store import VectorStore
from beigebox.storage.models import Message


def read_open_webui_db(db_path: str) -> list[dict]:
    """
    Read conversations from Open WebUI's SQLite database.
    Open WebUI stores chats as JSON blobs in a 'chat' table.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute("SELECT id, chat, created_at, updated_at FROM chat ORDER BY created_at").fetchall()
    except sqlite3.OperationalError:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        print(f"Could not find 'chat' table. Available tables: {[t['name'] for t in tables]}")
        conn.close()
        return []

    conversations = []
    for row in rows:
        try:
            chat_data = json.loads(row["chat"]) if isinstance(row["chat"], str) else row["chat"]
            conversations.append({
                "id": row["id"],
                "chat": chat_data,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        except (json.JSONDecodeError, TypeError) as e:
            print(f"  Skipping conversation {row['id']}: {e}")

    conn.close()
    return conversations


def extract_messages(chat_data: dict) -> list[dict]:
    """Extract messages from Open WebUI's chat JSON structure."""
    messages = []
    raw_messages = None

    if isinstance(chat_data, dict):
        if "messages" in chat_data:
            raw_messages = chat_data["messages"]
        elif "history" in chat_data and isinstance(chat_data["history"], dict):
            if "messages" in chat_data["history"]:
                raw_messages = chat_data["history"]["messages"]

    if not raw_messages:
        return messages

    if isinstance(raw_messages, dict):
        raw_messages = list(raw_messages.values())

    for msg in raw_messages:
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                messages.append({
                    "role": role,
                    "content": content,
                    "model": msg.get("model", chat_data.get("model", "")),
                    "timestamp": msg.get("timestamp", ""),
                })

    return messages


def main():
    parser = argparse.ArgumentParser(description="Import Open WebUI history")
    parser.add_argument("--source", "-s", required=True, help="Path to Open WebUI webui.db")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported without writing")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"Source database not found: {source}")
        sys.exit(1)

    print(f"Reading Open WebUI database: {source}")
    conversations = read_open_webui_db(str(source))
    print(f"Found {len(conversations)} conversations")

    total_messages = 0
    for conv in conversations:
        msgs = extract_messages(conv["chat"])
        total_messages += len(msgs)

    print(f"Total messages to import: {total_messages}")

    if args.dry_run:
        print("\n[DRY RUN] No data written. Remove --dry-run to import.")
        for conv in conversations[:5]:
            msgs = extract_messages(conv["chat"])
            print(f"  Conv {conv['id'][:12]}...: {len(msgs)} messages")
        if len(conversations) > 5:
            print(f"  ... and {len(conversations) - 5} more")
        return

    cfg = get_config()
    sqlite = SQLiteStore(cfg["storage"]["sqlite_path"])
    vector = VectorStore(
        chroma_path=cfg["storage"]["chroma_path"],
        embedding_model=cfg["embedding"]["model"],
        embedding_url=cfg["embedding"]["backend_url"],
    )

    imported = 0
    for conv in conversations:
        msgs = extract_messages(conv["chat"])
        for msg_data in msgs:
            msg = Message(
                conversation_id=conv["id"],
                role=msg_data["role"],
                content=msg_data["content"],
                model=msg_data.get("model", ""),
                timestamp=msg_data.get("timestamp", conv.get("created_at", "")),
            )
            sqlite.store_message(msg)
            vector.store_message(
                message_id=msg.id,
                conversation_id=conv["id"],
                role=msg.role,
                content=msg.content,
                model=msg.model,
                timestamp=msg.timestamp,
            )
            imported += 1

        if imported % 100 == 0:
            print(f"  Imported {imported} messages...")

    print(f"\nDone. Imported {imported} messages from {len(conversations)} conversations.")
    print(f"SQLite: {cfg['storage']['sqlite_path']}")
    print(f"ChromaDB: {cfg['storage']['chroma_path']}")


if __name__ == "__main__":
    main()
