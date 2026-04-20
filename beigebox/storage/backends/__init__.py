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

from .base import VectorBackend

_REGISTRY: dict[str, type[VectorBackend]] = {}


def _register():
    """Lazy-import backends to avoid hard dependencies at import time.

    PostgreSQL + pgvector is the primary backend. Lazy import ensures we only
    raise ImportError if the user explicitly tries to use a backend that's not installed.
    """
    global _REGISTRY
    if _REGISTRY:
        return
    try:
        from .postgres import PostgresBackend
        _REGISTRY["postgres"] = PostgresBackend
    except ImportError as e:
        raise ImportError(
            "PostgreSQL vector backend requires psycopg2 and pgvector. "
            "Install with: pip install psycopg2-binary pgvector"
        ) from e


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


__all__ = ["VectorBackend", "make_backend"]
