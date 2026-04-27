"""Subprocess wrappers for ruff and semgrep.

Both runners exec the tool as a subprocess, capture JSON output, parse, and
return a list of normalized findings plus a stats dict. Tool absence or non-zero
exit codes that don't carry usable JSON are turned into an error string instead
of raising, so the pipeline can still emit a partial result.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Severity / type mapping
# ---------------------------------------------------------------------------

# ruff rule prefix -> (severity, garlicpress type)
_RUFF_PREFIX_MAP: dict[str, tuple[str, str]] = {
    "F": ("medium", "logic_error"),    # pyflakes: undefined names, unused, etc.
    "E9": ("high", "logic_error"),     # syntax errors
    "B": ("medium", "logic_error"),    # bugbear
    "S": ("medium", "security"),       # bandit-port; bumped per-rule below
    "ASYNC": ("medium", "logic_error"),
    "ARG": ("low", "logic_error"),
    "RUF": ("low", "logic_error"),     # ruff-native
    "PL": ("low", "logic_error"),      # pylint subset
    "TRY": ("low", "logic_error"),
    "UP": ("low", "style"),
    "SIM": ("low", "style"),
    "I": ("low", "style"),
    "E": ("low", "style"),             # default for E (E9 caught above)
    "W": ("low", "style"),
    "N": ("low", "style"),
    "D": ("low", "style"),
}

# A few S-rules deserve a high severity bump (the actually dangerous ones).
_RUFF_S_HIGH = {
    "S102",  # exec
    "S301",  # pickle
    "S307",  # eval
    "S311",  # weak crypto random
    "S324",  # weak hash
    "S501",  # SSL no verify
    "S502",  # ssl insecure
    "S506",  # yaml load
    "S602",  # subprocess shell=True
    "S605",  # subprocess shell call
    "S608",  # SQL injection-likely string-build
    "S701",  # jinja2 autoescape off
}


def _classify_ruff(code: str) -> tuple[str, str]:
    """Map a ruff rule code (e.g. 'S301') to (severity, type)."""
    if not code:
        return ("low", "other")
    if code in _RUFF_S_HIGH:
        return ("high", "security")
    # Match longest prefix first so 'E9' beats 'E'.
    for prefix in sorted(_RUFF_PREFIX_MAP, key=len, reverse=True):
        if code.startswith(prefix):
            return _RUFF_PREFIX_MAP[prefix]
    return ("low", "other")


def _classify_semgrep(severity: str, metadata: dict) -> tuple[str, str]:
    sev_map = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
    sev = sev_map.get((severity or "").upper(), "low")
    category = (metadata or {}).get("category", "")
    if category in ("security", "vuln", "vulnerability"):
        ftype = "security"
    elif category in ("correctness", "best-practice"):
        ftype = "logic_error"
    elif category in ("performance",):
        ftype = "resource_leak"
    else:
        ftype = "other"
    return sev, ftype


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

async def run_ruff(
    repo_path: Path,
    *,
    select: str | None = None,
    ignore: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Run ``ruff check --output-format json`` on ``repo_path``.

    Returns:
        {"findings": [...], "stats": {...}, "error": str | None}
    """
    if shutil.which("ruff") is None:
        return _runner_error("ruff", "ruff not on PATH")

    args = ["ruff", "check", "--output-format", "json"]
    if select:
        args += ["--select", select]
    if ignore:
        args += ["--ignore", ignore]
    args.append(str(repo_path))

    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return _runner_error("ruff", f"timeout after {timeout}s")
    except FileNotFoundError:
        return _runner_error("ruff", "ruff binary not found")
    elapsed = round(time.monotonic() - started, 2)

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    # ruff exits 0 if no findings, 1 if findings, other on error. We need to
    # try to parse JSON regardless and fall back to the stderr on parse fail.
    try:
        data = json.loads(stdout) if stdout.strip() else []
    except json.JSONDecodeError as exc:
        return _runner_error("ruff", f"json parse failed: {exc}; stderr={stderr[:200]}")

    findings = []
    for item in data:
        code = item.get("code") or ""
        severity, ftype = _classify_ruff(code)
        loc = item.get("location") or {}
        line = loc.get("row", 0)
        col = loc.get("column", 0)
        path = item.get("filename", "")
        message = item.get("message", "")
        url = item.get("url", "")
        findings.append({
            "tool": "ruff",
            "rule_id": code,
            "severity": severity,
            "type": ftype,
            "file": path,
            "line": line,
            "column": col,
            "message": message,
            "url": url,
        })

    return {
        "findings": findings,
        "stats": {"duration_seconds": elapsed, "raw_count": len(data)},
        "error": None,
    }


async def run_semgrep(
    repo_path: Path,
    *,
    config: str = "p/python",
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Run ``semgrep scan --json --config <config>`` on ``repo_path``.

    Default config is ``p/python``, the canonical Python rule pack from the
    semgrep registry. Other useful configs: ``p/security-audit``, ``p/owasp-top-ten``.

    Returns:
        {"findings": [...], "stats": {...}, "error": str | None}
    """
    if shutil.which("semgrep") is None:
        return _runner_error("semgrep", "semgrep not on PATH")

    args = [
        "semgrep", "scan",
        "--json",
        "--quiet",
        "--no-git-ignore",  # don't skip files just because gitignored — fuzz already does this
        "--config", config,
        str(repo_path),
    ]

    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return _runner_error("semgrep", f"timeout after {timeout}s")
    except FileNotFoundError:
        return _runner_error("semgrep", "semgrep binary not found")
    elapsed = round(time.monotonic() - started, 2)

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    try:
        data = json.loads(stdout) if stdout.strip() else {"results": []}
    except json.JSONDecodeError as exc:
        return _runner_error("semgrep", f"json parse failed: {exc}; stderr={stderr[:200]}")

    findings = []
    for r in data.get("results", []):
        extra = r.get("extra") or {}
        metadata = extra.get("metadata") or {}
        severity, ftype = _classify_semgrep(extra.get("severity", ""), metadata)
        start = r.get("start") or {}
        findings.append({
            "tool": "semgrep",
            "rule_id": r.get("check_id") or "",
            "severity": severity,
            "type": ftype,
            "file": r.get("path", ""),
            "line": start.get("line", 0),
            "column": start.get("col", 0),
            "message": extra.get("message", ""),
            "url": metadata.get("source", "") or metadata.get("references", [""])[0] if metadata.get("references") else "",
        })

    return {
        "findings": findings,
        "stats": {
            "duration_seconds": elapsed,
            "raw_count": len(data.get("results", [])),
            "errors": data.get("errors", []),
        },
        "error": None,
    }


def _runner_error(tool: str, msg: str) -> dict[str, Any]:
    return {
        "findings": [],
        "stats": {"duration_seconds": 0.0, "raw_count": 0},
        "error": f"{tool}: {msg}",
    }
