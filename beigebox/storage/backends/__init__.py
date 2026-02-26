"""
Vector backend factory.

Usage:
    from beigebox.storage.backends import make_backend
    backend = make_backend("chromadb", path="./data/chroma")

Adding a new backend:
    1. Create beigebox/storage/backends/<name>.py implementing VectorBackend.
    2. Add an entry to _REGISTRY below.
    3. Set  storage.vector_backend: <name>  in config.yaml.
    No other changes required.
"""

from .base import VectorBackend

_REGISTRY: dict[str, type[VectorBackend]] = {}


def _register():
    """Lazy-import backends to avoid hard dependencies at import time."""
    global _REGISTRY
    if _REGISTRY:
        return
    from .chroma import ChromaBackend
    _REGISTRY["chromadb"] = ChromaBackend


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
