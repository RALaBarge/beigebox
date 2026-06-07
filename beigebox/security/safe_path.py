"""
Path containment for user-supplied paths.

Wraps `pathlib.Path` to enforce that a value resolves under an allowed
base directory. The single resolve+relative_to comparison catches symlink
escape, `..` traversal, and absolute-path escape in one shot.

Usage
-----

    from beigebox.security.safe_path import SafePath, UnsafePathError

    base = Path("/var/lib/beigebox/audit")
    try:
        p = SafePath(user_value, base=base)
    except UnsafePathError:
        return 403
    p.path.write_text(...)              # `.path` is a real pathlib.Path

Or as a one-liner:

    from beigebox.security.safe_path import resolve_under
    p = resolve_under(user_value, base=...)

This module deliberately does NOT validate filesystem permissions, file
type, or content — those are the caller's responsibility. It only answers
the single question: "does this path stay inside the box."
"""

from __future__ import annotations

from pathlib import Path


class UnsafePathError(Exception):
    """Raised when a path resolves outside its allowed base directory."""


class SafePath:
    """
    A path that has been verified to live under an allowed base directory.

    Construction:
      * Resolves the candidate path (follows symlinks, collapses `..`).
      * Resolves the base directory.
      * Asserts the candidate is `relative_to(base)`.

    The candidate need NOT exist (so this is safe for write-target paths),
    but the base SHOULD exist — otherwise you cannot meaningfully assert
    that anything lives under it.
    """

    __slots__ = ("path", "base")

    def __init__(self, value: str | Path, *, base: str | Path):
        if not isinstance(value, (str, Path)):
            raise UnsafePathError(
                f"path must be str or Path, got {type(value).__name__}"
            )
        if value == "":
            raise UnsafePathError("path must be a non-empty value")

        base_resolved = Path(base).resolve()

        # Treat absolute inputs as absolute; relative inputs as base-relative.
        # In both cases the resolve() below collapses `..` and follows symlinks.
        candidate = Path(value) if Path(value).is_absolute() else (base_resolved / value)

        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as e:
            raise UnsafePathError(f"cannot resolve {value!r}: {e}") from e

        try:
            resolved.relative_to(base_resolved)
        except ValueError as e:
            raise UnsafePathError(
                f"{value!r} resolves to {resolved}, outside base {base_resolved}"
            ) from e

        self.path = resolved
        self.base = base_resolved

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return f"SafePath({str(self.path)!r}, base={str(self.base)!r})"


def resolve_under(value: str | Path, *, base: str | Path) -> Path:
    """One-shot SafePath: return the validated, resolved Path or raise."""
    return SafePath(value, base=base).path
