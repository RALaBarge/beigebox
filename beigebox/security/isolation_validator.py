"""
Isolation-First Parameter Validator — Phase 3 Security Hardening

LESSON FROM CLAUDE CODE LEAK:
Regex-based blocklists WILL be bypassed. This validator uses isolation as the primary
defense mechanism:

  1. Isolation (strongest) — Path can only exist in specific locations
  2. Allowlist (strict) — Only explicitly allowed values accepted
  3. Semantic (detection) — Additional pattern-based checks
  4. Logging (observability) — Every decision is logged for forensics

This design assumes an active adversary with knowledge of the validation logic.

Reference: GMO Flatt Security research on Claude Code bypasses
  - Argument abbreviation (git --upload-pa vs --upload-pack)
  - Chained variable expansion (${var@P})
  - Undocumented command options (sed e, man --html)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class IsolationCheckResult:
    """Result of an isolation check."""
    allowed: bool
    reason: str
    canonical_path: Optional[str] = None


class IsolationValidator:
    """
    Validates parameters using isolation-first approach.

    Philosophy:
      - Don't try to catch all attacks with regex
      - Make attacks impossible through isolation
      - Log everything to catch what bypasses through
    """

    def __init__(
        self,
        workspace_root: str | Path,
        allow_write_dirs: Set[str] | None = None,
        allow_read_dirs: Set[str] | None = None,
    ):
        """
        Args:
            workspace_root: Root of sandboxed workspace
            allow_write_dirs: Relative dirs where writes are allowed (default: out/)
            allow_read_dirs: Relative dirs where reads are allowed (default: in/, out/)
        """
        self.workspace_root = Path(workspace_root).resolve()

        # Ensure workspace root exists and is a directory
        if not self.workspace_root.is_dir():
            raise ValueError(f"workspace_root must exist and be a directory: {self.workspace_root}")

        # Define allowed read/write zones
        self.allow_read_dirs = allow_read_dirs or {
            str(self.workspace_root / "in"),
            str(self.workspace_root / "out"),
            str(self.workspace_root / "tmp"),
        }
        self.allow_write_dirs = allow_write_dirs or {
            str(self.workspace_root / "out"),
            str(self.workspace_root / "tmp"),
        }

        logger.info(f"Isolation validator initialized: root={self.workspace_root}")
        logger.info(f"Read dirs: {self.allow_read_dirs}")
        logger.info(f"Write dirs: {self.allow_write_dirs}")

    def validate_path_read(self, path: str) -> IsolationCheckResult:
        """
        Validate a path for READ access.

        Checks:
          1. Path must exist
          2. Path must resolve under workspace
          3. Path must be in allowed read directory
          4. No symlinks escape workspace
        """
        return self._validate_path(path, action="read")

    def validate_path_write(self, path: str) -> IsolationCheckResult:
        """
        Validate a path for WRITE access.

        Checks:
          1. Parent directory must exist
          2. Path must resolve under workspace
          3. Path must be in allowed write directory
          4. No symlinks escape workspace
        """
        return self._validate_path(path, action="write")

    def _validate_path(self, path: str, action: str) -> IsolationCheckResult:
        """
        Core path validation logic.

        Returns early with DENY on ANY failure condition.
        Logs all checks for forensics.
        """

        # --- Step 1: Normalize input ---
        if not path or not isinstance(path, str):
            logger.warning(f"[ISOLATION] Invalid path input: {path!r}")
            return IsolationCheckResult(
                allowed=False,
                reason="Path must be non-empty string"
            )

        # Remove leading/trailing whitespace (attackers use unicode spaces)
        path = path.strip()

        # --- Step 2: Reject absolute paths outright ---
        # Absolute paths are inherently risky. Relative paths are better
        # because we control the base directory.
        if path.startswith("/") or path.startswith("\\"):
            logger.warning(f"[ISOLATION] Absolute path rejected: {path!r}")
            return IsolationCheckResult(
                allowed=False,
                reason="Absolute paths not allowed. Use relative paths from workspace root."
            )

        # --- Step 3: Reject dangerous patterns ---
        # These are BONUS checks. Even if bypassed, isolation catches it.
        dangerous_patterns = [
            "..",           # Directory traversal
            "~",            # Home directory
            "$",            # Variable expansion
            "`",            # Command substitution
            "(",            # Subshell
            "&",            # Background/AND
            "|",            # Pipe
            ";",            # Command separator
            "\x00",         # Null byte
            "\n",           # Newline (command injection)
        ]

        for pattern in dangerous_patterns:
            if pattern in path:
                logger.warning(f"[ISOLATION] Dangerous pattern '{pattern}' in path: {path!r}")
                return IsolationCheckResult(
                    allowed=False,
                    reason=f"Path contains dangerous character: {pattern!r}"
                )

        # --- Step 4: Resolve to canonical path ---
        try:
            # Try to resolve relative to workspace
            candidate = (self.workspace_root / path).resolve()
        except (OSError, RuntimeError) as e:
            logger.warning(f"[ISOLATION] Failed to resolve path {path!r}: {e}")
            return IsolationCheckResult(
                allowed=False,
                reason=f"Path resolution failed: {e}"
            )

        # --- Step 5: Check isolation boundary ---
        # This is the KEY check. After resolution, is it still under workspace?
        try:
            relative = candidate.relative_to(self.workspace_root)
        except ValueError:
            # Candidate is OUTSIDE workspace. DENY.
            logger.critical(
                f"[ISOLATION] Path ESCAPES workspace: {path!r} -> {candidate} (outside {self.workspace_root})"
            )
            return IsolationCheckResult(
                allowed=False,
                reason=f"Path escapes workspace boundary"
            )

        # --- Step 6: Check action-specific constraints ---
        if action == "read":
            # For reads, path must exist
            if not candidate.exists():
                logger.warning(f"[ISOLATION] Path does not exist for read: {candidate}")
                return IsolationCheckResult(
                    allowed=False,
                    reason=f"Path does not exist: {candidate}"
                )

            # Check it's in allowed read directories
            if not self._is_in_allowed_dirs(candidate, self.allow_read_dirs):
                logger.critical(
                    f"[ISOLATION] Read path not in allowed dirs: {candidate}"
                )
                return IsolationCheckResult(
                    allowed=False,
                    reason=f"Path is not in allowed read directories"
                )

        elif action == "write":
            # For writes, parent directory must exist
            parent = candidate.parent
            if not parent.is_dir():
                logger.warning(f"[ISOLATION] Parent directory missing for write: {parent}")
                return IsolationCheckResult(
                    allowed=False,
                    reason=f"Parent directory must exist: {parent}"
                )

            # Check it's in allowed write directories
            if not self._is_in_allowed_dirs(candidate, self.allow_write_dirs):
                logger.critical(
                    f"[ISOLATION] Write path not in allowed dirs: {candidate}"
                )
                return IsolationCheckResult(
                    allowed=False,
                    reason=f"Path is not in allowed write directories"
                )

        # --- Step 7: Symlink check ---
        # Even if path is resolved and in boundary, check no symlinks
        # point outside workspace
        if candidate.is_symlink():
            # Symlinks are suspicious. Reject them.
            logger.warning(f"[ISOLATION] Symlink detected (rejected): {candidate} -> {candidate.readlink()}")
            return IsolationCheckResult(
                allowed=False,
                reason="Symlinks not allowed"
            )

        # Check all parent directories for symlinks
        for parent in candidate.parents:
            if parent == self.workspace_root.parent:
                break
            if parent.is_symlink():
                logger.critical(
                    f"[ISOLATION] Symlink in path chain: {parent}"
                )
                return IsolationCheckResult(
                    allowed=False,
                    reason=f"Symlink detected in path chain: {parent}"
                )

        # --- SUCCESS ---
        logger.info(f"[ISOLATION] ✓ Path allowed: {path!r} -> {candidate}")
        return IsolationCheckResult(
            allowed=True,
            reason="Path validated",
            canonical_path=str(candidate)
        )

    def _is_in_allowed_dirs(self, path: Path, allowed_dirs: Set[str]) -> bool:
        """Check if path is within any allowed directory."""
        for allowed in allowed_dirs:
            allowed_path = Path(allowed).resolve()
            try:
                path.relative_to(allowed_path)
                return True
            except ValueError:
                continue
        return False

    def validate_url_scheme(self, url: str) -> IsolationCheckResult:
        """
        Validate URL scheme. Only http/https allowed.
        Rejects: javascript, data, file, ftp, gopher, ldap, etc.
        """
        if not url:
            return IsolationCheckResult(allowed=False, reason="URL cannot be empty")

        try:
            # Simple scheme extraction (don't import urllib to minimize deps)
            if "://" not in url:
                return IsolationCheckResult(
                    allowed=False,
                    reason="Invalid URL format (missing scheme)"
                )

            scheme = url.split("://")[0].lower()

            # Allowlist only http/https
            if scheme not in {"http", "https"}:
                logger.warning(f"[ISOLATION] Dangerous URL scheme: {scheme!r} in {url!r}")
                return IsolationCheckResult(
                    allowed=False,
                    reason=f"URL scheme '{scheme}' not allowed. Only http/https permitted."
                )

            logger.info(f"[ISOLATION] ✓ URL scheme valid: {scheme}")
            return IsolationCheckResult(allowed=True, reason="URL scheme valid")

        except Exception as e:
            logger.error(f"[ISOLATION] Error validating URL: {e}")
            return IsolationCheckResult(allowed=False, reason=f"URL validation error: {e}")

    def validate_command_name(self, cmd: str) -> IsolationCheckResult:
        """
        Validate command name against allowlist.
        This prevents invoking dangerous tools entirely.
        """
        # Allowlist of commands allowed to run
        # This is the strongest defense: just don't allow dangerous commands
        ALLOWED_COMMANDS = {
            # Safe data processing
            "cat", "head", "tail", "wc", "grep", "sed", "awk", "cut",
            # Safe directory operations
            "ls", "find", "stat", "file",
            # Safe compression
            "tar", "gzip", "bzip2", "xz",
            # Safe hashing
            "md5sum", "sha256sum", "sha512sum",
            # Safe networking (very restricted)
            "curl", "wget", "nc", "netcat",
            # Safe shell utilities
            "echo", "printf", "test", "expr",
        }

        # Extract base command name (before first space or pipe)
        base_cmd = cmd.split()[0] if cmd else ""
        base_cmd = base_cmd.strip()

        if base_cmd in ALLOWED_COMMANDS:
            logger.info(f"[ISOLATION] ✓ Command allowed: {base_cmd}")
            return IsolationCheckResult(allowed=True, reason="Command in allowlist")
        else:
            logger.critical(f"[ISOLATION] Command NOT in allowlist: {base_cmd}")
            return IsolationCheckResult(
                allowed=False,
                reason=f"Command '{base_cmd}' not in allowlist"
            )
