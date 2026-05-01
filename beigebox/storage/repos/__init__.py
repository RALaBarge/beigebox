"""
storage/repos — per-entity repositories on top of BaseDB.

Each repo takes a BaseDB instance (injected) and exposes entity-shaped methods.
Use the factory functions here rather than instantiating repos directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beigebox.storage.db.base import BaseDB

from beigebox.storage.repos.api_keys import ApiKeyRepo
from beigebox.storage.repos.quarantine import QuarantineRepo


def make_api_key_repo(db: "BaseDB") -> ApiKeyRepo:
    """Create an ApiKeyRepo backed by the given BaseDB.

    The caller owns the db lifecycle (creation, close).  The repo owns the
    api_keys schema; call repo.create_tables() before first use.
    """
    return ApiKeyRepo(db)


def make_quarantine_repo(db: "BaseDB") -> QuarantineRepo:
    """Create a QuarantineRepo backed by the given BaseDB.

    The caller owns the db lifecycle. The repo owns the
    quarantined_embeddings schema; call repo.create_tables() before first use.
    """
    return QuarantineRepo(db)


__all__ = [
    "ApiKeyRepo",
    "make_api_key_repo",
    "QuarantineRepo",
    "make_quarantine_repo",
]
