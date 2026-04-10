#!/usr/bin/env python3
"""
ingest_to_chroma.py — embed markdown files into ChromaDB without moving them.

The existing auto-ingest path (beigebox/main.py:_sync_ingest_staging) reads from
2600/2600-staging/ and ARCHIVES files into 2600/2599/ after embedding. We don't
want that side-effect for the bulk SF/Jira ingests — we want the markdown to
stay put in workspace/out/rag/{SF,JIRA}/ for direct inspection. This script
calls the same chunking and store primitives directly without the file move.

Usage (inside the beigebox container):

    docker exec beigebox python3 /app/scripts/ingest_to_chroma.py \\
        --src /app/workspace/out/rag/SF \\
        --tag SF

    docker exec beigebox python3 /app/scripts/ingest_to_chroma.py \\
        --src /app/workspace/out/rag/JIRA \\
        --tag JIRA

Idempotent: ChromaDB upsert key is content-addressed by blob hash, so re-running
on the same files is a no-op (ids collide and overwrite identically).
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from pathlib import Path

# Allow execution as `python /app/scripts/ingest_to_chroma.py`
sys.path.insert(0, "/app")

from beigebox.config import get_config, get_primary_backend_url  # noqa: E402
from beigebox.storage.backends import make_backend  # noqa: E402
from beigebox.storage.chunker import chunk_text  # noqa: E402
from beigebox.storage.vector_store import VectorStore  # noqa: E402


def _build_vector_store(log: logging.Logger) -> VectorStore:
    """Build a VectorStore the same way main.py:188 does, reading config."""
    cfg = get_config()
    embed_cfg = (cfg.get("embedding") or {})
    storage_cfg = (cfg.get("storage") or {}).get("vector_store") or {}
    backend_type = storage_cfg.get("backend", "chromadb")
    backend_path = storage_cfg.get("path", "/app/data/chroma")
    embedding_model = embed_cfg.get("model", "nomic-embed-text")
    embedding_url = embed_cfg.get("backend_url") or get_primary_backend_url(cfg)
    log.info("  vector backend: %s @ %s", backend_type, backend_path)
    log.info("  embedding:      %s @ %s", embedding_model, embedding_url)
    return VectorStore(
        embedding_model=embedding_model,
        embedding_url=embedding_url,
        backend=make_backend(backend_type, path=backend_path),
    )


def setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ingest_to_chroma")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", required=True, help="Source directory of *.md files")
    p.add_argument("--tag", default="", help="Optional tag prefix on source_file metadata (e.g. 'SF')")
    p.add_argument("--log", default="/app/logs/ingest_to_chroma.log")
    p.add_argument("--chunk-chars", type=int, default=1200)
    p.add_argument("--overlap-chars", type=int, default=150)
    p.add_argument("--limit", type=int, default=0, help="Stop after N files (debugging)")
    args = p.parse_args()

    log = setup_logging(Path(args.log))
    src = Path(args.src)
    if not src.exists():
        log.error("source dir does not exist: %s", src)
        sys.exit(1)

    log.info("=" * 70)
    log.info("ingest_to_chroma starting")
    log.info("  src:           %s", src)
    log.info("  tag prefix:    %s", args.tag or "(none)")
    log.info("  chunk_chars:   %d", args.chunk_chars)
    log.info("  overlap_chars: %d", args.overlap_chars)

    files = sorted(src.glob("*.md"))
    if args.limit > 0:
        files = files[: args.limit]
    log.info("  files found:   %d", len(files))

    if not files:
        log.warning("no files to ingest — exiting")
        return

    vs = _build_vector_store(log)
    log.info("  vector store ready")

    total_chunks = 0
    total_files_ok = 0
    total_files_fail = 0
    t0 = time.monotonic()

    for idx, fp in enumerate(files, start=1):
        try:
            content = fp.read_text(encoding="utf-8")
            md5 = hashlib.md5(content.encode()).hexdigest()
            # source_file is what shows up in document_search results — tag prefix
            # makes it easy to filter by data source ("SF/00300001..." vs "JIRA/SX-1234...")
            source_label = f"{args.tag}/{fp.name}" if args.tag else fp.name
            chunks = chunk_text(
                content,
                chunk_chars=args.chunk_chars,
                overlap_chars=args.overlap_chars,
                source_file=source_label,
            )
            for chunk in chunks:
                vs.store_document_chunk(
                    source_file=source_label,
                    chunk_index=chunk["chunk_index"],
                    char_offset=chunk["char_offset"],
                    blob_hash=md5,
                    text=chunk["text"],
                )
            total_chunks += len(chunks)
            total_files_ok += 1
            if idx % 50 == 0 or idx == len(files):
                elapsed = time.monotonic() - t0
                rate = idx / elapsed if elapsed > 0 else 0
                log.info("[%d/%d] %s → %d chunks (rate %.1f files/s, total chunks %d)",
                         idx, len(files), fp.name, len(chunks), rate, total_chunks)
        except Exception as e:
            total_files_fail += 1
            log.error("[%d/%d] %s ✗ %s: %s", idx, len(files), fp.name, type(e).__name__, e)

    elapsed = time.monotonic() - t0
    log.info("=" * 70)
    log.info("ingest_to_chroma finished")
    log.info("  files OK:     %d", total_files_ok)
    log.info("  files failed: %d", total_files_fail)
    log.info("  total chunks: %d", total_chunks)
    log.info("  elapsed:      %.1fs", elapsed)
    log.info("=" * 70)


if __name__ == "__main__":
    main()
