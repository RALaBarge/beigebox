"""Subprocess wrappers for ruff, semgrep, and mypy.

Each runner execs the tool as a subprocess, captures output, parses, and
returns a list of normalized findings plus a stats dict. Tool absence or non-zero
exit codes that don't carry usable output are turned into an error string instead
of raising, so the pipeline can still emit a partial result.
"""

from __future__ import annotations

import asyncio
import json
import re
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


# mypy error codes that signal "this almost certainly crashes at runtime" —
# bumped from medium to high so a triage view surfaces them first.
_MYPY_HIGH_CODES = {
    "attr-defined",     # accessing a missing attribute
    "union-attr",       # accessing attribute on Optional that may be None
    "call-arg",         # missing/extra positional/keyword argument
    "arg-type",         # passing wrong type to a parameter
    "return-value",     # returning wrong type from a function
    "assignment",       # incompatible types in assignment
    "operator",         # operator on incompatible types
    "index",            # indexing a non-indexable type
    "no-redef",         # redefinition of name
    "valid-type",       # not a valid type
}


def _classify_mypy(level: str, code: str) -> tuple[str, str]:
    """Map a mypy (level, error-code) tuple to (severity, garlicpress type).

    Notes are informational ("revealed type X" etc.) so they go to low. Errors
    default to medium/logic_error; a curated set of "would crash at runtime"
    codes bumps to high.
    """
    lvl = (level or "").lower()
    if lvl == "note":
        return ("low", "logic_error")
    if lvl != "error":
        return ("low", "other")
    if code in _MYPY_HIGH_CODES:
        return ("high", "logic_error")
    return ("medium", "logic_error")


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


# mypy line format with the flags we pass:
#   path/to/file.py:LINE:COL: SEVERITY: MESSAGE  [error-code]
# COL is optional (always present with --show-column-numbers).
# error-code is optional (always present with --show-error-codes), but old
# mypy versions sometimes emit notes without one.
_MYPY_LINE_RE = re.compile(
    r"^(?P<file>[^:]+):"
    r"(?P<line>\d+)"
    r"(?::(?P<col>\d+))?"
    r":\s*(?P<level>error|note|warning):\s*"
    r"(?P<message>.*?)"
    r"(?:\s+\[(?P<code>[a-zA-Z0-9_-]+)\])?"
    r"\s*$"
)


def _parse_mypy_output(stdout: str) -> list[dict[str, Any]]:
    """Parse mypy text output into runner-shape dicts.

    mypy's stable output is a one-line-per-diagnostic format. We use the
    parseable flags (`--show-column-numbers --show-error-codes --no-pretty
    --no-error-summary`) to make it regex-friendly. JSON output exists in
    newer mypy but its schema has shifted across releases — text is more stable.
    """
    findings: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = _MYPY_LINE_RE.match(line)
        if not m:
            continue  # summary lines, "Found N errors", etc.
        level = m.group("level") or ""
        code = m.group("code") or ""
        severity, ftype = _classify_mypy(level, code)
        findings.append({
            "tool": "mypy",
            "rule_id": code or level,
            "severity": severity,
            "type": ftype,
            "file": m.group("file") or "",
            "line": int(m.group("line") or 0),
            "column": int(m.group("col") or 0),
            "message": m.group("message") or "",
            "url": "",
        })
    return findings


async def run_mypy(
    repo_path: Path,
    *,
    strict: bool = False,
    follow_imports: str = "silent",
    extra_args: list[str] | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Run ``mypy`` on ``repo_path``.

    Defaults are tuned for "scan an arbitrary repo without an env":
    ``--ignore-missing-imports`` so missing third-party stubs don't bury
    real findings, ``--follow-imports=silent`` so untyped deps don't get
    re-checked, and ``--show-column-numbers --show-error-codes --no-pretty``
    so output is parseable.

    Returns:
        {"findings": [...], "stats": {...}, "error": str | None}
    """
    if shutil.which("mypy") is None:
        return _runner_error("mypy", "mypy not on PATH")

    args = [
        "mypy",
        "--ignore-missing-imports",
        f"--follow-imports={follow_imports}",
        "--show-column-numbers",
        "--show-error-codes",
        "--no-pretty",
        "--no-error-summary",
        "--no-incremental",  # cleaner subprocess invocation; sacrifices cache
    ]
    if strict:
        args.append("--strict")
    if extra_args:
        args.extend(extra_args)
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
        return _runner_error("mypy", f"timeout after {timeout}s")
    except FileNotFoundError:
        return _runner_error("mypy", "mypy binary not found")
    elapsed = round(time.monotonic() - started, 2)

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    findings = _parse_mypy_output(stdout)

    # mypy returns 0 if no errors, 1 if errors, 2 if it crashed itself. A
    # crash means the parser ate the whole input — surface stderr as the error.
    if proc.returncode == 2 and not findings:
        return _runner_error("mypy", f"crashed (rc=2): {stderr[:300]}")

    return {
        "findings": findings,
        "stats": {"duration_seconds": elapsed, "raw_count": len(findings)},
        "error": None,
    }


def _runner_error(tool: str, msg: str) -> dict[str, Any]:
    return {
        "findings": [],
        "stats": {"duration_seconds": 0.0, "raw_count": 0},
        "error": f"{tool}: {msg}",
    }
