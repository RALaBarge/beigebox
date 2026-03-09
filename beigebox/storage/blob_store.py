"""
BlobStore — content-addressed gzip blob storage.

Stores arbitrary text content as gzip-compressed files addressed by the
SHA-256 hash of the content.  Used by operator tool capture and document
indexing pipelines.

Layout on disk:
    {blobs_dir}/{hash[:2]}/{hash}.gz

The 2-char prefix subdirectory keeps directory listing fast for large stores.
Files are immutable once written — the hash is the identity.  Writing the same
content twice is a no-op (natural dedup).
"""
from __future__ import annotations

import gzip
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class BlobStore:
    def __init__(self, blobs_dir: Path):
        self._dir = Path(blobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        logger.info("BlobStore initialised at %s", self._dir)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _blob_path(self, blob_hash: str) -> Path:
        return self._dir / blob_hash[:2] / f"{blob_hash}.gz"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, content: str) -> str:
        """Store content and return its SHA-256 hex digest."""
        raw = content.encode("utf-8")
        blob_hash = hashlib.sha256(raw).hexdigest()
        path = self._blob_path(blob_hash)
        # Idempotent write: same content → same hash → same path. If the file
        # already exists we skip compression and writing entirely (free dedup).
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wb") as f:
                f.write(raw)
            logger.debug(
                "BlobStore wrote %s (%d bytes compressed)",
                blob_hash[:8], path.stat().st_size,
            )
        return blob_hash

    def read(self, blob_hash: str) -> str:
        """Load and decompress a blob by hash.  Raises FileNotFoundError if missing."""
        path = self._blob_path(blob_hash)
        with gzip.open(path, "rb") as f:
            return f.read().decode("utf-8")

    def path(self, blob_hash: str) -> Path:
        """Return the filesystem path for a blob without loading it."""
        return self._blob_path(blob_hash)

    def exists(self, blob_hash: str) -> bool:
        return self._blob_path(blob_hash).exists()

    def count(self) -> int:
        """Return total number of stored blobs."""
        return sum(1 for _ in self._dir.rglob("*.gz"))
