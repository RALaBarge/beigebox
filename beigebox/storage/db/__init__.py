"""
Generic SQL DB shim — factory + lazy registry.

Mirrors ``beigebox/storage/backends/__init__.py`` (vector storage):

    from beigebox.storage.db import make_db, BaseDB

    db = make_db("sqlite", path="data/beigebox.db")
    # …or…
    db = make_db("postgres", connection_string="postgresql://localhost/beigebox")
    # …or…
    db = make_db("memory")  # in-process SQLite

Adding a new backend:
    1. Create ``beigebox/storage/db/<name>.py`` implementing :class:`BaseDB`.
    2. Add an entry to ``_REGISTRY`` below.
    3. Set ``storage.db.type: <name>`` (and any kwargs) in config.yaml.
"""
from __future__ import annotations

import os
import threading
from typing import Any

from beigebox.storage.db.base import BaseDB

_REGISTRY: dict[str, type[BaseDB]] = {}
_REGISTRY_LOCK = threading.Lock()


def _register() -> None:
    """Lazy-import backends to avoid hard deps at import time.

    SQLite is stdlib so always loads. PostgreSQL needs psycopg2 — we try
    to import it but tolerate failure (pip extra, may not be installed in
    every deployment). MemoryDB is a SQLite subclass so always available.
    """
    global _REGISTRY
    if _REGISTRY:
        return
    with _REGISTRY_LOCK:
        if _REGISTRY:
            return
        try:
            from beigebox.storage.db.sqlite import SqliteDB
            _REGISTRY["sqlite"] = SqliteDB
        except ImportError as e:
            import warnings
            warnings.warn(f"SqliteDB unavailable: {e}", RuntimeWarning)
        try:
            from beigebox.storage.db.memory import MemoryDB
            _REGISTRY["memory"] = MemoryDB
        except ImportError as e:
            import warnings
            warnings.warn(f"MemoryDB unavailable: {e}", RuntimeWarning)
        try:
            from beigebox.storage.db.postgres import PostgresDB
            _REGISTRY["postgres"] = PostgresDB
        except ImportError as e:
            import warnings
            warnings.warn(
                f"PostgresDB unavailable ({e}); install psycopg2-binary to enable.",
                RuntimeWarning,
            )


def make_db(backend_type: str, **kwargs: Any) -> BaseDB:
    """Instantiate a DB shim by name.

    Args:
        backend_type: registry key — ``"sqlite"``, ``"postgres"``, or ``"memory"``.
        **kwargs: passed directly to the backend's ``__init__``.

    Raises:
        ValueError: if the backend type is not registered.
    """
    _register()
    cls = _REGISTRY.get(backend_type)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(
            f"Unknown DB backend: {backend_type!r}. Available: {available}"
        )
    return cls(**kwargs)


def build_db_kwargs(cfg: dict, *, default_sqlite_path: str | None = None) -> tuple[str, dict]:
    """Resolve (backend_type, kwargs) for ``make_db`` from project config.

    Reads ``cfg.storage.db.{type, path, connection_string, pool_size}`` (or
    falls back to sane defaults). Centralized so callers — server startup,
    CLI, tests — agree on which DB to talk to.

    Falls back to ``DATABASE_URL`` env var if ``cfg.storage.db.connection_string``
    isn't set and the type is ``postgres``.
    """
    storage_cfg = cfg.get("storage", {}) or {}
    db_cfg = storage_cfg.get("db", {}) or {}
    backend_type = db_cfg.get("type", "sqlite")

    if backend_type == "postgres":
        return backend_type, {
            "connection_string": db_cfg.get(
                "connection_string",
                os.environ.get("DATABASE_URL", "postgresql://localhost/beigebox"),
            ),
            "pool_size": db_cfg.get("pool_size", 5),
        }
    if backend_type == "memory":
        return backend_type, {}
    # sqlite (default)
    return backend_type, {
        "path": db_cfg.get("path", default_sqlite_path or "./data/beigebox.db"),
        "timeout": db_cfg.get("timeout", 30.0),
        "wal": db_cfg.get("wal", True),
        "foreign_keys": db_cfg.get("foreign_keys", True),
    }


__all__ = ["BaseDB", "make_db", "build_db_kwargs"]
