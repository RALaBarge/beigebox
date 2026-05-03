"""Logging + payload-log configuration for the lifespan startup path.

Two concerns live here:

1. ``_setup_logging(cfg)`` — install the stdlib root logger with the level
   and (optional) file handler from ``logging`` config. Falls back to
   stderr-only if the configured log directory isn't writable.
2. Payload-log path binding — calls ``beigebox.payload_log.configure(...)``
   so the module-level path is set before any request handler runs.

Both run unconditionally at the start of ``bootstrap.startup``; neither
returns anything that needs to live on AppState.
"""
from __future__ import annotations

import logging
from pathlib import Path

from beigebox.payload_log import configure as _pl_configure


def _setup_logging(cfg: dict) -> None:
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure permissions are correct for non-root users
        try:
            log_path.parent.chmod(0o755)
            handlers.append(logging.FileHandler(log_file))
        except (OSError, PermissionError) as e:
            # If file logging fails, fall back to stderr only
            print(f"Warning: Could not set up file logging to {log_file}: {e}", flush=True)
            print("Logging to stderr only", flush=True)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def configure_logging_and_payload_log(cfg: dict) -> None:
    """Run both side-effecting setup steps in lifespan order."""
    _setup_logging(cfg)
    _pl_configure(cfg.get("payload_log", {}).get("path", "./data/payload.jsonl"))


__all__ = ["_setup_logging", "configure_logging_and_payload_log"]
