"""Fan-out skill — list of inputs → N parallel model calls → optional reduce."""

from .pipeline import fan_out

__all__ = ["fan_out"]
