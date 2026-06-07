"""
Plugin loader safety helpers.

Plugins in BeigeBox are operator-trusted code loaded in-process (see
BEIGEBOX_IS_NOT.md, "Plugin model"). This module enforces two cheap
defenses-in-depth before the loaders `exec_module` anything:

  1. The plugin directory must be under the project root and not
     world-writable. (Prevents config-driven path escape and obvious
     filesystem-tampering vectors.)

  2. Only files whose stem appears in an explicit allow-list are loaded.
     (Prevents an attacker who lands a file in the plugin dir from
     auto-executing on next restart, and forces the operator to declare
     intent for every loaded plugin.)

Neither defense is sandboxing. Plugins still run in-process with full
Python privileges. The trust model is: the operator is responsible for the
contents of files they put on the allow-list.
"""

from __future__ import annotations

import logging
import stat
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class UnsafePluginDirError(Exception):
    """Raised when a plugin directory fails the safety preconditions."""


def safe_plugin_dir(path: str | Path, *, project_root: Path) -> Path | None:
    """
    Validate that a plugin directory is safe to scan and load from.

    Returns the resolved Path on success, or None when the directory simply
    doesn't exist (caller should treat as "no plugins to load"). Raises
    UnsafePluginDirError for safety violations — the caller is expected to
    catch and refuse to load.

    Checks:
      * Resolved path is under project_root (no `..` escape from a
        config-driven `plugins_dir`).
      * The directory is not world-writable. Group-writable is permitted
        (operator may want plugins editable by their group); the bright
        line is `o+w`.
    """
    resolved = Path(path).resolve()
    project_root_resolved = project_root.resolve()

    try:
        resolved.relative_to(project_root_resolved)
    except ValueError as e:
        raise UnsafePluginDirError(
            f"Plugin directory {resolved} escapes project root {project_root_resolved}"
        ) from e

    if not resolved.exists():
        logger.debug("Plugin directory not found: %s", resolved)
        return None

    if not resolved.is_dir():
        raise UnsafePluginDirError(f"Plugin path {resolved} is not a directory")

    mode = stat.S_IMODE(resolved.stat().st_mode)
    if mode & stat.S_IWOTH:
        raise UnsafePluginDirError(
            f"Plugin directory {resolved} is world-writable (mode={oct(mode)}); "
            f"refusing to load. Run: chmod o-w {resolved}"
        )

    return resolved


def filter_by_allowlist(
    files: Iterable[Path],
    allowed: list[str] | None,
    *,
    context: str,
) -> Iterable[Path]:
    """
    Filter plugin files by an explicit allow-list of stems.

    Args:
        files: candidate plugin file paths.
        allowed: explicit allow-list of file stems (e.g. ["llama_cpp", "executorch"]),
            or None to fall back to load-everything (with a deprecation warning).
        context: short label used in log messages (e.g. "backend_plugin").

    Behavior:
        allowed=None  → log a deprecation warning and yield every file.
        allowed=[]    → yield nothing.
        allowed=[…]   → yield only files whose `.stem` is in the list.
    """
    files = list(files)

    if allowed is None:
        logger.warning(
            "%s: no explicit allow-list configured. Loading all %d file(s) for "
            "backwards compatibility — declare an `allowed: [...]` list in "
            "config.yaml to silence this warning and pin the loaded set.",
            context,
            len(files),
        )
        yield from files
        return

    allowed_set = set(allowed)
    for f in files:
        if f.stem in allowed_set:
            yield f
        else:
            logger.debug("%s: skipping %s (not in allow-list)", context, f.name)
