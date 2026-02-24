"""
system_context.py — Hot-reloadable global system prompt injection.

Reads system_context.md from the project root and injects its contents
as a system message at the top of every proxied request. Hot-reloaded
on every request via mtime check — no restart needed.

Disabled by default. Enable in config.yaml:
    system_context:
        enabled: true
        path: ./system_context.md   # optional, default shown

Or toggle at runtime via POST /api/v1/config:
    {"system_context_enabled": true}

The file can also be read/written via the HTTP API:
    GET  /api/v1/system-context       → returns current file contents
    POST /api/v1/system-context       → writes new contents to the file
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Hot-reload state
_context_text: str = ""
_context_mtime: float = 0.0
_context_path: Path | None = None


def _get_path(cfg: dict) -> Path:
    """Resolve the system_context.md path from config."""
    sc_cfg = cfg.get("system_context", {})
    raw = sc_cfg.get("path", "./system_context.md")
    return Path(raw)


def get_system_context(cfg: dict) -> str:
    """
    Return the current system context text, hot-reloading if the file changed.
    Returns empty string if disabled, missing, or empty.
    """
    global _context_text, _context_mtime, _context_path

    # Check enabled — runtime config takes precedence
    from beigebox.config import get_runtime_config
    rt = get_runtime_config()

    sc_cfg = cfg.get("system_context", {})
    enabled = rt.get("system_context_enabled", sc_cfg.get("enabled", False))
    if not enabled:
        return ""

    path = _get_path(cfg)

    # Resolve once and cache
    if _context_path is None or _context_path != path:
        _context_path = path
        _context_mtime = 0.0  # Force reload on path change

    if not path.exists():
        if _context_text:
            logger.debug("system_context.md not found — context cleared")
            _context_text = ""
            _context_mtime = 0.0
        return ""

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _context_text

    if mtime == _context_mtime:
        return _context_text

    # File changed — reload
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text != _context_text:
            logger.info("system_context.md reloaded (%d chars)", len(text))
        _context_text = text
        _context_mtime = mtime
    except Exception as e:
        logger.warning("Failed to reload system_context.md: %s", e)

    return _context_text


def inject_system_context(body: dict, cfg: dict) -> dict:
    """
    Inject system context into the request body's messages list.

    If a system message already exists at position 0, the context is
    prepended to it. Otherwise a new system message is inserted at the front.
    Returns the (possibly modified) body.
    """
    context = get_system_context(cfg)
    if not context:
        return body

    messages = body.get("messages", [])
    if not messages:
        return body

    # Check for existing system message at position 0
    if messages[0].get("role") == "system":
        existing = messages[0].get("content", "")
        messages[0]["content"] = f"{context}\n\n{existing}" if existing else context
    else:
        messages.insert(0, {"role": "system", "content": context})

    body["messages"] = messages
    return body


def read_context_file(cfg: dict) -> str:
    """Read and return the raw contents of system_context.md (for the API)."""
    path = _get_path(cfg)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to read system_context.md: %s", e)
        return ""


def write_context_file(cfg: dict, content: str) -> bool:
    """Write new contents to system_context.md and bust the mtime cache."""
    global _context_mtime
    path = _get_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _context_mtime = 0.0  # Force hot-reload on next request
        logger.info("system_context.md written (%d chars)", len(content))
        return True
    except Exception as e:
        logger.warning("Failed to write system_context.md: %s", e)
        return False
