"""Fuzz skill — discover, score, and fuzz Python functions in a repo."""

from .pipeline import run_fuzzing

__all__ = ["run_fuzzing"]
