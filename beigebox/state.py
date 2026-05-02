"""Singleton accessor for the FastAPI ``AppState``.

Extracted out of main.py so the new ``beigebox/routers/`` modules can
import ``get_state`` without creating a circular dependency
(routers → main → routers). main.py imports this module instead of
defining the singleton itself.

Lifespan calls ``set_state(...)`` once after building the AppState.
Endpoints (regardless of which router module they live in) call
``get_state()`` at request time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beigebox.app_state import AppState


_app_state: "AppState | None" = None


def get_state() -> "AppState":
    """Return the initialized AppState. Raises if called before startup."""
    if _app_state is None:
        raise RuntimeError("AppState not initialized — server not started yet")
    return _app_state


def set_state(state: "AppState") -> None:
    """Bind the application state singleton — called once from main.py lifespan."""
    global _app_state
    _app_state = state


def maybe_state() -> "AppState | None":
    """Return the AppState if initialized, else None.

    For shutdown paths and modules that need to defensively probe state
    without raising. main.py lifespan shutdown uses this to clean up
    egress hooks + close the WireLog only when those subsystems exist.
    """
    return _app_state
