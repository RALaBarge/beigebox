"""
Subprocess runner shared by all security tool wrappers.

Hard rules:
  - argv list only. NEVER pass shell=True. NEVER f-string user input into a
    single command string (HexStrike's primary security flaw).
  - Hard timeout per call. No interactive tools.
  - Binary-missing returns a structured error string, not an exception, so
    the MCP server stays usable when the host has only some tools installed.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default per-tool wall-clock cap. Per-tool overrides via run_argv(timeout=).
DEFAULT_TIMEOUT_SECONDS = 600  # 10 min
# Cap stderr/stdout returned to the MCP client (full output goes nowhere — by
# design; tools that produce structured JSONL/JSON should parse it themselves).
MAX_STDOUT_BYTES = 256 * 1024
MAX_STDERR_BYTES = 32 * 1024


@dataclass
class RunResult:
    ok: bool
    binary: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    error: str | None = None

    def to_json_str(self) -> str:
        d = {
            "ok": self.ok,
            "binary": self.binary,
            "argv": self.argv,
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 2),
            "stdout": self.stdout[:MAX_STDOUT_BYTES],
            "stderr": self.stderr[:MAX_STDERR_BYTES],
        }
        if self.error:
            d["error"] = self.error
        return json.dumps(d, ensure_ascii=False)


def which(binary: str) -> str | None:
    """Return absolute path to *binary* on PATH, or None."""
    return shutil.which(binary)


def run_argv(
    argv: list[str],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> RunResult:
    """
    Run *argv* with a hard timeout and capture stdout/stderr.

    *argv* MUST be a list (subprocess invoked with shell=False). The first
    element is the binary; we resolve it via shutil.which to give a clean
    'binary not installed' message when it's missing.
    """
    import os
    import time

    if not argv:
        return RunResult(False, "", [], -1, "", "", 0.0, error="empty argv")

    binary = argv[0]
    resolved = which(binary)
    if resolved is None:
        return RunResult(
            False, binary, argv, -1, "", "", 0.0,
            error=f"binary '{binary}' not found on PATH (install it to enable this tool)",
        )

    full_argv = [resolved, *argv[1:]]
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            full_argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return RunResult(
            False, binary, full_argv, -1, "", "", elapsed,
            error=f"timeout after {timeout}s",
        )
    except FileNotFoundError as exc:
        elapsed = time.monotonic() - start
        return RunResult(
            False, binary, full_argv, -1, "", "", elapsed,
            error=f"FileNotFoundError: {exc}",
        )
    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning("subprocess failed for %s: %s", binary, exc)
        return RunResult(
            False, binary, full_argv, -1, "", "", elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )

    elapsed = time.monotonic() - start
    return RunResult(
        ok=(proc.returncode == 0),
        binary=binary,
        argv=full_argv,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        duration_s=elapsed,
    )
