"""
Base class shared by every security tool wrapper. Mirrors the shape that
beigebox/tools/registry.py expects: a `description: str` and a
`run(input_str: str) -> str` method. Input is JSON.

Also provides input safety helpers (target whitelisting, no shell metachars
in args) so individual wrappers don't reinvent them.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Conservative target validator: hostname or IPv4/IPv6 (with optional CIDR or
# port). Reject shell metacharacters outright.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_FORBIDDEN_CHARS = set(";|&`$<>(){}[]\\\"'\n\r\t ")


class SecurityTool:
    """Subclass and implement `_run(parsed: dict) -> str | dict`.

    Subclasses set ``requires_auth = True`` to opt into the authorization
    gate — the base ``run()`` then refuses execution unless the input
    contains ``"authorization": true``. This replaces the per-class
    inline check that used to live in every destructive wrapper.
    """

    name: str = ""
    binary: str = ""
    description: str = ""
    capture_tool_io: bool = True
    max_context_chars: int = 16_000
    # Opt-in authorization gate for destructive tools (hydra, john, hashcat,
    # netexec, msfvenom, all impacket AD tools, sqlmap with -os-shell, etc.).
    requires_auth: bool = False

    def run(self, input_str: str) -> str:
        """Parse JSON input and dispatch to the wrapper-specific _run."""
        try:
            parsed = json.loads(input_str) if input_str.strip() else {}
        except json.JSONDecodeError as exc:
            return self._err(f"input must be valid JSON: {exc}")
        if not isinstance(parsed, dict):
            return self._err("input must be a JSON object")
        if self.requires_auth and not parsed.get("authorization"):
            return self._err(
                "this tool is destructive and requires explicit authorization — "
                "set 'authorization': true in the input to confirm you have "
                "permission to run it against the target"
            )
        try:
            result = self._run(parsed)
        except Exception as exc:  # defensive — never crash the registry
            logger.exception("%s wrapper failed", self.name)
            return self._err(f"unexpected error: {type(exc).__name__}: {exc}")
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    # --- subclasses override this ---
    def _run(self, parsed: dict) -> str | dict:
        raise NotImplementedError

    # --- helpers shared across tools ---
    @staticmethod
    def _err(msg: str) -> str:
        return json.dumps({"ok": False, "error": msg})

    @staticmethod
    def safe_target(target: str, *, allow_url: bool = False, allow_cidr: bool = True) -> bool:
        """
        Conservative whitelist for command-line targets.

        Returns True iff target is a hostname, IPv4/IPv6, optionally with /CIDR
        or :port, OR (if allow_url) an http(s) URL whose host is itself safe.
        """
        if not target or len(target) > 2048:
            return False
        if any(ch in target for ch in _FORBIDDEN_CHARS):
            return False
        if allow_url and (target.startswith("http://") or target.startswith("https://")):
            try:
                from urllib.parse import urlparse
                u = urlparse(target)
                if not u.hostname:
                    return False
                return SecurityTool.safe_target(u.hostname, allow_url=False, allow_cidr=False)
            except Exception:
                return False
        # CIDR
        if allow_cidr and "/" in target:
            host, _, mask = target.partition("/")
            if not mask.isdigit():
                return False
            target = host
        # ip:port
        if target.count(":") == 1 and target.rsplit(":", 1)[1].isdigit():
            target = target.rsplit(":", 1)[0]
        # IP literal?
        try:
            ipaddress.ip_address(target)
            return True
        except ValueError:
            pass
        return bool(_HOSTNAME_RE.match(target))

    @staticmethod
    def safe_arg(arg: Any, *, max_len: int = 2048) -> bool:
        """Reject any free-form arg containing shell metachars, newlines, or
        exceeding *max_len* characters. The length cap matches safe_target."""
        if not isinstance(arg, str):
            return False
        if len(arg) > max_len:
            return False
        return not any(ch in arg for ch in _FORBIDDEN_CHARS)

    @staticmethod
    def safe_path(path: str, *, must_exist: bool = True,
                  forbid_traversal: bool = False) -> bool:
        """
        Validate a filesystem path for tools that read local files (binwalk,
        exiftool, john, etc.). Rejects shell metachars; optionally verifies
        the path exists.

        ``forbid_traversal=True`` additionally rejects paths whose textual
        form contains ``..`` segments — used for tools that *write* (msfvenom)
        to defend against an LLM being prompted into ``../../etc/cron.d/evil``.
        Read-only tools default to ``forbid_traversal=False`` because the
        operator is trusted to point at any file they have permission to read.
        """
        from pathlib import PurePosixPath
        if not path or not isinstance(path, str) or len(path) > 4096:
            return False
        if any(ch in path for ch in _FORBIDDEN_CHARS - {"/"}):
            return False
        if forbid_traversal:
            # Reject any '..' path segment, even after normalization.
            parts = PurePosixPath(path).parts
            if any(p == ".." for p in parts):
                return False
        if must_exist:
            from pathlib import Path
            if not Path(path).exists():
                return False
        return True
