"""
Vector backend factory.

Usage:
    from beigebox.storage.backends import make_backend
    backend = make_backend("postgres", connection_string="postgresql://localhost/beigebox")

Adding a new backend:
    1. Create beigebox/storage/backends/<name>.py implementing VectorBackend.
    2. Add an entry to _REGISTRY below.
    3. Set  storage.vector_backend: <name>  in config.yaml.
    No other changes required.
"""

import os
import threading

from .base import VectorBackend

_REGISTRY: dict[str, type[VectorBackend]] = {}
_REGISTRY_LOCK = threading.Lock()


def _register():
    """Lazy-import backends to avoid hard dependencies at import time.

    PostgreSQL + pgvector is the primary production backend.
    MemoryBackend is a hermetic in-memory option used by the test suite and
    available for ephemeral workloads.

    Each import is independently guarded — failing to load one backend
    doesn't disqualify the others. Thread-safe: the populate step is
    serialized so concurrent first-callers can't see a partially-built
    registry.
    """
    global _REGISTRY
    # Fast path: registry already built, no lock needed
    if _REGISTRY:
        return
    with _REGISTRY_LOCK:
        # Re-check under the lock — another thread may have populated while
        # we were waiting
        if _REGISTRY:
            return
        try:
            from .memory import MemoryBackend
            _REGISTRY["memory"] = MemoryBackend
        except ImportError as e:
            # numpy is a hard project dep — this should never fire — but keep
            # the registry construction tolerant rather than crashing import.
            import warnings
            warnings.warn(f"MemoryBackend unavailable: {e}", RuntimeWarning)
        try:
            from .postgres import PostgresBackend
            _REGISTRY["postgres"] = PostgresBackend
        except ImportError as e:
            # Postgres is the production backend; without it we surface a clear
            # error. Memory-only operation is still possible for tests.
            import warnings
            warnings.warn(
                f"PostgresBackend unavailable ({e}); only memory backend registered. "
                "Install with: pip install psycopg2-binary pgvector",
                RuntimeWarning,
            )


def make_backend(backend_type: str, **kwargs) -> VectorBackend:
    """
    Instantiate a vector backend by name.

    Args:
        backend_type: Registry key (e.g. "chromadb").
        **kwargs:     Passed directly to the backend constructor.

    Raises:
        ValueError: If the backend type is not registered.
    """
    _register()
    cls = _REGISTRY.get(backend_type)
    if cls is None:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(
            f"Unknown vector backend: '{backend_type}'. "
            f"Available: {available}"
        )
    return cls(**kwargs)


def build_backend_kwargs(cfg: dict, vector_store_path) -> tuple[str, dict]:
    """Resolve (backend_type, kwargs) for `make_backend` from project config.

    Postgres needs a connection_string; path-based backends (memory, legacy
    chromadb) need a path. Centralized so CLI and server agree.
    """
    storage_cfg = cfg.get("storage", {})
    backend_type = storage_cfg.get("vector_backend", "postgres")
    if backend_type == "postgres":
        pg_cfg = storage_cfg.get("postgres", {})
        return backend_type, {
            "connection_string": pg_cfg.get(
                "connection_string",
                os.environ.get("DATABASE_URL", "postgresql://localhost/beigebox"),
            ),
            "pool_size": pg_cfg.get("pool_size", 5),
        }
    return backend_type, {"path": vector_store_path}


__all__ = ["VectorBackend", "make_backend", "build_backend_kwargs"]
