"""Static analysis skill — ruff + semgrep + mypy + pip-audit + detect-secrets,
garlicpress-shape findings."""

from .pipeline import run_static

__all__ = ["run_static"]
