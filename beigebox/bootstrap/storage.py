"""Storage subsystem bootstrap.

Builds (in dependency order):

  - BaseDB shim shared by every per-entity repo
  - Five repos: api_keys, conversations, quarantine, users, wire_events
  - Storage backend (sqlite|chroma|postgres) for the vector store
  - RAGPoisoningDetector (optional)
  - VectorStore (consumes backend + poisoning_detector + quarantine)
  - BlobStore (filesystem-only)
  - CostTracker (optional)

Returns a ``StorageBundle`` so the orchestrator can pull each piece into
``AppState`` without pretending storage is one big happy object.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beigebox.config import get_primary_backend_url, get_storage_paths
from beigebox.costs import CostTracker
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector
from beigebox.storage.backends import (
    build_backend_kwargs as _build_backend_kwargs,
    make_backend as _make_backend,
)
from beigebox.storage.blob_store import BlobStore
from beigebox.storage.db import build_db_kwargs, make_db
from beigebox.storage.repos import (
    make_api_key_repo,
    make_conversation_repo,
    make_quarantine_repo,
    make_user_repo,
    make_wire_event_repo,
)
from beigebox.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class StorageBundle:
    db: Any
    api_keys: Any
    conversations: Any
    quarantine: Any
    users: Any
    wire_events: Any
    vector_store: VectorStore
    blob_store: BlobStore
    poisoning_detector: RAGPoisoningDetector | None
    cost_tracker: CostTracker | None
    sqlite_path: str
    vector_store_path: str


def build_storage(cfg: dict) -> StorageBundle:
    """Build the full storage stack. Caller passes ``cfg`` (already loaded)."""
    sqlite_path, vector_store_path = get_storage_paths(cfg)
    integrity_cfg = cfg.get("security", {}).get("memory_integrity", {})

    # BaseDB shim — shared by every per-entity repo (api_keys, conversations,
    # quarantine, users, wire_events). The SQLiteStore god-object is gone in
    # batch B; ConversationRepo owns the conversations + messages tables and
    # all integrity-validation state previously held inside SQLiteStore.
    db_type, db_kwargs = build_db_kwargs(cfg, default_sqlite_path=sqlite_path)
    db = make_db(db_type, **db_kwargs)
    api_keys = make_api_key_repo(db)
    api_keys.create_tables()
    conversations = make_conversation_repo(db, integrity_config=integrity_cfg)
    conversations.create_tables()
    quarantine = make_quarantine_repo(db)
    quarantine.create_tables()
    users = make_user_repo(db)
    users.create_tables()
    wire_events = make_wire_event_repo(db)
    wire_events.create_tables()

    embed_cfg = cfg["embedding"]
    backend_type, backend_kwargs = _build_backend_kwargs(cfg, vector_store_path)

    # RAG poisoning detection initialization
    poisoning_detector: RAGPoisoningDetector | None = None
    poisoning_cfg = cfg.get("embedding_poisoning_detection", {})
    if poisoning_cfg.get("enabled", True):
        poisoning_detector = RAGPoisoningDetector(
            sensitivity=poisoning_cfg.get("sensitivity", 0.95),
            baseline_window=poisoning_cfg.get("baseline_window", 1000),
            min_norm=poisoning_cfg.get("min_norm", 0.1),
            max_norm=poisoning_cfg.get("max_norm", 100.0),
        )
        logger.info(
            "RAG poisoning detection: ENABLED (sensitivity=%.2f)",
            poisoning_cfg.get("sensitivity", 0.95),
        )
    else:
        logger.warning("RAG poisoning detection: DISABLED")

    vector_store = VectorStore(
        embedding_model=embed_cfg["model"],
        embedding_url=embed_cfg.get("backend_url") or get_primary_backend_url(cfg),
        backend=_make_backend(backend_type, **backend_kwargs),
        poisoning_detector=poisoning_detector,
        quarantine=quarantine,
    )
    blob_store = BlobStore(Path(vector_store_path) / "blobs")

    # Cost tracker (v0.6) — depends on db
    cost_tracker: CostTracker | None = None
    if cfg.get("cost_tracking", {}).get("enabled", False):
        cost_tracker = CostTracker(db)
        logger.info("Cost tracking: enabled")
    else:
        logger.info("Cost tracking: disabled")

    return StorageBundle(
        db=db,
        api_keys=api_keys,
        conversations=conversations,
        quarantine=quarantine,
        users=users,
        wire_events=wire_events,
        vector_store=vector_store,
        blob_store=blob_store,
        poisoning_detector=poisoning_detector,
        cost_tracker=cost_tracker,
        sqlite_path=sqlite_path,
        vector_store_path=vector_store_path,
    )


__all__ = ["StorageBundle", "build_storage"]
