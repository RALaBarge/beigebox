"""Subprocess wrappers for ruff, semgrep, mypy, pip-audit, and detect-secrets.

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
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            await _kill_proc(proc)
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
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            await _kill_proc(proc)
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
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            await _kill_proc(proc)
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


async def _kill_proc(proc: asyncio.subprocess.Process) -> None:
    """Kill a child process and reap it. ``asyncio.wait_for`` only cancels the
    awaiting task — the OS-level child keeps running until the parent waits on
    it. Without this, a hung scanner leaks a zombie subprocess."""
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass  # tried our best; OS will clean up when the parent exits.


# ---------------------------------------------------------------------------
# pip-audit (SCA — dependency CVE scanning)
# ---------------------------------------------------------------------------

# Manifest files pip-audit can consume via -r. We deliberately exclude
# pyproject.toml: pip-audit only audits pyproject if it's a project root and
# usually wants the env installed; --no-deps requirements scanning is the
# fast, deterministic mode for "scan an arbitrary repo".
_PIP_AUDIT_MANIFEST_GLOBS = (
    "requirements.txt",
    "requirements-*.txt",
    "requirements_*.txt",
    "requirements/*.txt",
)

# Skip these directories when discovering manifests — vendored deps and
# test fixtures inflate noise without finding real product vulns.
_PIP_AUDIT_SKIP_DIRS = {
    ".venv", "venv", ".tox", "node_modules", ".git",
    "__pycache__", "site-packages", "dist", "build",
    ".eggs", ".mypy_cache", ".pytest_cache",
}


def _discover_pip_manifests(repo_path: Path) -> list[Path]:
    """Find requirements*.txt files at repo top + one level deep.

    Skips vendored/cache dirs. Doesn't recurse arbitrarily — a 10k-file repo
    with random fixture requirements files would otherwise blow the budget.
    """
    found: list[Path] = []
    for pattern in _PIP_AUDIT_MANIFEST_GLOBS:
        for p in repo_path.glob(pattern):
            if p.is_file() and not any(part in _PIP_AUDIT_SKIP_DIRS for part in p.parts):
                found.append(p)
    # One level deep (e.g. requirements/dev.txt, services/*/requirements.txt)
    for child in repo_path.iterdir():
        if not child.is_dir() or child.name in _PIP_AUDIT_SKIP_DIRS:
            continue
        for pattern in ("requirements.txt", "requirements-*.txt", "requirements_*.txt"):
            for p in child.glob(pattern):
                if p.is_file():
                    found.append(p)
    # Dedupe while preserving order
    seen: set[Path] = set()
    out: list[Path] = []
    for p in found:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def _grep_package_line(manifest: Path, pkg_name: str) -> int:
    """Find the 1-based line number where ``pkg_name`` is pinned in ``manifest``.

    Returns 0 if not found. Best-effort — pip-audit doesn't tell us where in
    the file a given package is, so we approximate so the location field
    points somewhere useful.
    """
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    needle = pkg_name.lower().replace("_", "-")
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip().lower()
        # Match "name==", "name>=", "name~=", "name " (loose pin), bare "name"
        if stripped.startswith(needle):
            after = stripped[len(needle):len(needle) + 1]
            if not after or after in "=<>~!@ ;[#":
                return i
    return 0


async def run_pip_audit(
    repo_path: Path,
    *,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Run ``pip-audit -r <manifest> --format json --no-deps`` on each
    discovered requirements file and aggregate the results.

    ``--no-deps`` keeps the audit deterministic (no transitive resolution,
    no network resolves beyond the OSV/PyPI vulnerability lookup) and keeps
    runtime predictable in CI. Missing manifests => skip silently with a
    note, not an error.

    Returns:
        {"findings": [...], "stats": {...}, "error": str | None}
    """
    if shutil.which("pip-audit") is None:
        return _runner_error("pip-audit", "pip-audit not on PATH")

    manifests = _discover_pip_manifests(repo_path)
    if not manifests:
        return {
            "findings": [],
            "stats": {"duration_seconds": 0.0, "raw_count": 0, "manifests_scanned": 0},
            "error": None,  # Not having a requirements.txt isn't an error.
        }

    started = time.monotonic()
    findings: list[dict[str, Any]] = []
    raw_total = 0
    per_manifest_errors: list[str] = []

    for manifest in manifests:
        try:
            rel_manifest = str(manifest.resolve().relative_to(repo_path))
        except ValueError:
            rel_manifest = manifest.name

        args = [
            "pip-audit",
            "-r", str(manifest),
            "--format", "json",
            "--no-deps",
            "--progress-spinner", "off",
        ]
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            if proc is not None:
                await _kill_proc(proc)
            per_manifest_errors.append(f"{rel_manifest}: timeout after {timeout}s")
            continue
        except FileNotFoundError:
            return _runner_error("pip-audit", "pip-audit binary disappeared mid-run")

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        # pip-audit exits 0 if no vulns, 1 if vulns. JSON is on stdout regardless.
        if not stdout.strip():
            if proc.returncode not in (0, 1):
                per_manifest_errors.append(f"{rel_manifest}: rc={proc.returncode} {stderr[:200]}")
            continue

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            per_manifest_errors.append(f"{rel_manifest}: json parse failed: {exc}")
            continue

        for dep in data.get("dependencies", []):
            name = dep.get("name", "")
            version = dep.get("version", "")
            for vuln in dep.get("vulns", []):
                raw_total += 1
                vid = vuln.get("id", "")
                aliases = vuln.get("aliases", []) or []
                fix_versions = vuln.get("fix_versions", []) or []
                description = vuln.get("description", "") or ""
                # CVE alias is the headline for humans; PYSEC/GHSA is what pip-audit emits as id.
                cve = next((a for a in aliases if a.startswith("CVE-")), "")
                rule_id = vid or cve or "vuln"
                line = _grep_package_line(manifest, name) if name else 0
                msg_head = f"{name}=={version}: {cve or vid}"
                msg = (msg_head + " — " + description.strip().split("\n")[0])[:400]
                findings.append({
                    "tool": "pip_audit",
                    "rule_id": rule_id,
                    "severity": "high",  # CVE in a pinned dep -> high; we don't have CVSS to refine.
                    "type": "security",
                    "file": rel_manifest,
                    "line": line,
                    "column": 0,
                    "message": msg,
                    "url": f"https://osv.dev/vulnerability/{vid}" if vid else "",
                    # Stash extra context for downstream consumers / triage.
                    "extra": {
                        "package": name,
                        "version": version,
                        "fix_versions": fix_versions,
                        "aliases": aliases,
                    },
                })

    elapsed = round(time.monotonic() - started, 2)
    # Surface per-manifest failures regardless of whether other manifests
    # produced findings. Hiding them when at least one manifest succeeded
    # would mask flaky/timed-out scans on the rest.
    error = "; ".join(per_manifest_errors) if per_manifest_errors else None
    return {
        "findings": findings,
        "stats": {
            "duration_seconds": elapsed,
            "raw_count": raw_total,
            "manifests_scanned": len(manifests),
            "manifest_errors": per_manifest_errors,
        },
        "error": error,
    }


# ---------------------------------------------------------------------------
# detect-secrets (secrets in source)
# ---------------------------------------------------------------------------

# Detector types where the false-positive rate is high enough that we don't
# want to scream "high severity" at every match. Caller gets them at medium;
# the Yelp KeywordDetector and entropy detectors trigger on a lot of fixtures
# / test data / hashes-that-aren't-secrets.
_SECRETS_LOW_CONFIDENCE_TYPES = {
    "Secret Keyword",
    "Base64 High Entropy String",
    "Hex High Entropy String",
}


async def run_secrets(
    repo_path: Path,
    *,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Run ``detect-secrets scan <repo>`` and emit one finding per detected secret.

    detect-secrets walks the repo, runs all bundled detectors, and emits a JSON
    document keyed by file. We map each entry to a high-severity security
    finding. Detectors that are notorious for FPs (KeywordDetector, entropy)
    are downgraded to medium so triage isn't drowned by hashes-in-tests.

    Returns:
        {"findings": [...], "stats": {...}, "error": str | None}
    """
    if shutil.which("detect-secrets") is None:
        return _runner_error("detect-secrets", "detect-secrets not on PATH")

    # --all-files: don't restrict to git-tracked files. The default behavior
    # (`git ls-files`) silently returns nothing for a non-git directory, which
    # would mask findings on a fixture / temp dir / pre-commit working tree.
    #
    # We pass "." and `cwd=repo_path` rather than the absolute path: detect-secrets
    # 1.5 silently returns an empty result set when the scan target is given as
    # an absolute path. Running from inside the repo with a relative target is
    # the supported invocation, and it makes the emitted filenames repo-relative.
    args = ["detect-secrets", "scan", "--all-files", "."]

    started = time.monotonic()
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo_path),
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            await _kill_proc(proc)
        return _runner_error("detect-secrets", f"timeout after {timeout}s")
    except FileNotFoundError:
        return _runner_error("detect-secrets", "detect-secrets binary not found")
    elapsed = round(time.monotonic() - started, 2)

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if not stdout.strip():
        if proc.returncode != 0:
            return _runner_error("detect-secrets", f"rc={proc.returncode}: {stderr[:200]}")
        return {
            "findings": [],
            "stats": {"duration_seconds": elapsed, "raw_count": 0},
            "error": None,
        }

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return _runner_error("detect-secrets", f"json parse failed: {exc}; stderr={stderr[:200]}")

    findings: list[dict[str, Any]] = []
    results = data.get("results") or {}
    raw_total = 0
    for filename, entries in results.items():
        for entry in entries or []:
            raw_total += 1
            secret_type = entry.get("type", "secret")
            line_number = entry.get("line_number", 0) or 0
            is_verified = bool(entry.get("is_verified", False))
            if is_verified:
                severity = "critical"
            elif secret_type in _SECRETS_LOW_CONFIDENCE_TYPES:
                severity = "medium"
            else:
                severity = "high"
            findings.append({
                "tool": "detect_secrets",
                "rule_id": secret_type,
                "severity": severity,
                "type": "security",
                "file": filename,
                "line": line_number,
                "column": 0,
                "message": (
                    f"Possible {secret_type} at {filename}:{line_number}"
                    + (" (VERIFIED LIVE)" if is_verified else "")
                ),
                "url": "",
                "extra": {
                    "is_verified": is_verified,
                    "hashed_secret": entry.get("hashed_secret", ""),
                },
            })

    return {
        "findings": findings,
        "stats": {"duration_seconds": elapsed, "raw_count": raw_total},
        "error": None,
    }
