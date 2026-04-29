"""Static analysis pipeline. Importable from a Trinity run or any other orchestrator.

Runs ``ruff``, ``semgrep``, ``mypy``, ``pip-audit``, and ``detect-secrets``
concurrently, normalizes their output to garlicpress-shape Finding dicts,
and returns a single result so static-analysis output can be merged with
fuzz findings without translation.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from .runners import run_mypy, run_pip_audit, run_ruff, run_secrets, run_semgrep


# Ruff selection bias: emphasize the "looks like a real bug" rules and skip
# pure-style noise. Caller can override via ruff_select / ruff_ignore.
DEFAULT_RUFF_SELECT = "F,E9,B,S,ASYNC,ARG,RUF,PL,TRY"

# Semgrep default config. Caller can pass any config string semgrep accepts
# (registry shortcut like ``p/python``, a path to a YAML rule file, or a
# comma-separated list).
DEFAULT_SEMGREP_CONFIG = "p/python"


async def run_static(
    repo_path: str | Path,
    *,
    ruff_select: str | None = DEFAULT_RUFF_SELECT,
    ruff_ignore: str | None = None,
    semgrep_config: str | None = DEFAULT_SEMGREP_CONFIG,
    mypy_strict: bool = False,
    mypy_follow_imports: str = "silent",
    mypy_extra_args: list[str] | None = None,
    enable_ruff: bool = True,
    enable_semgrep: bool = True,
    enable_mypy: bool = True,
    enable_pip_audit: bool = True,
    enable_secrets: bool = True,
    ruff_timeout: float = 120.0,
    semgrep_timeout: float = 600.0,
    mypy_timeout: float = 300.0,
    pip_audit_timeout: float = 180.0,
    secrets_timeout: float = 180.0,
    logger: Callable | None = None,
) -> dict[str, Any]:
    """Run ruff + semgrep + mypy + pip-audit + detect-secrets against ``repo_path``,
    return garlicpress-shape findings.

    Returns:
        {
          "findings": [garlicpress-shape Finding dicts],
          "stats": {<runner>_count, total_findings,
                    <runner>_duration_seconds, <runner>_error
                    for each of ruff/semgrep/mypy/pip_audit/secrets},
          "raw_results": {"ruff": {...}, "semgrep": {...}, "mypy": {...},
                          "pip_audit": {...}, "secrets": {...}},
        }
    """
    repo_path = Path(repo_path).resolve()
    if logger:
        logger(f"static analysis on {repo_path}")

    tasks: list[asyncio.Task] = []
    task_names: list[str] = []
    if enable_ruff:
        tasks.append(asyncio.create_task(
            run_ruff(repo_path, select=ruff_select, ignore=ruff_ignore, timeout=ruff_timeout)
        ))
        task_names.append("ruff")
    if enable_semgrep and semgrep_config:
        tasks.append(asyncio.create_task(
            run_semgrep(repo_path, config=semgrep_config, timeout=semgrep_timeout)
        ))
        task_names.append("semgrep")
    if enable_mypy:
        tasks.append(asyncio.create_task(
            run_mypy(
                repo_path,
                strict=mypy_strict,
                follow_imports=mypy_follow_imports,
                extra_args=mypy_extra_args,
                timeout=mypy_timeout,
            )
        ))
        task_names.append("mypy")
    if enable_pip_audit:
        tasks.append(asyncio.create_task(
            run_pip_audit(repo_path, timeout=pip_audit_timeout)
        ))
        task_names.append("pip_audit")
    if enable_secrets:
        tasks.append(asyncio.create_task(
            run_secrets(repo_path, timeout=secrets_timeout)
        ))
        task_names.append("secrets")

    if not tasks:
        return _empty_result()

    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    raw: dict[str, Any] = {}
    for name, result in zip(task_names, gathered):
        if isinstance(result, Exception):
            raw[name] = {
                "findings": [],
                "stats": {"duration_seconds": 0.0, "raw_count": 0},
                "error": f"{type(result).__name__}: {result}",
            }
        else:
            raw[name] = result
        if logger and raw[name].get("error"):
            logger(f"{name} error: {raw[name]['error']}")

    findings: list[dict[str, Any]] = []
    for name in task_names:
        for f in raw[name].get("findings", []):
            findings.append(_to_finding(f, repo_path))

    findings = _dedupe(findings)
    findings.sort(key=_severity_sort_key)

    stats = {
        "total_findings": len(findings),
        "ruff_count": len(raw.get("ruff", {}).get("findings", [])),
        "semgrep_count": len(raw.get("semgrep", {}).get("findings", [])),
        "mypy_count": len(raw.get("mypy", {}).get("findings", [])),
        "pip_audit_count": len(raw.get("pip_audit", {}).get("findings", [])),
        "secrets_count": len(raw.get("secrets", {}).get("findings", [])),
        "ruff_duration_seconds": raw.get("ruff", {}).get("stats", {}).get("duration_seconds", 0.0),
        "semgrep_duration_seconds": raw.get("semgrep", {}).get("stats", {}).get("duration_seconds", 0.0),
        "mypy_duration_seconds": raw.get("mypy", {}).get("stats", {}).get("duration_seconds", 0.0),
        "pip_audit_duration_seconds": raw.get("pip_audit", {}).get("stats", {}).get("duration_seconds", 0.0),
        "secrets_duration_seconds": raw.get("secrets", {}).get("stats", {}).get("duration_seconds", 0.0),
        "ruff_error": raw.get("ruff", {}).get("error"),
        "semgrep_error": raw.get("semgrep", {}).get("error"),
        "mypy_error": raw.get("mypy", {}).get("error"),
        "pip_audit_error": raw.get("pip_audit", {}).get("error"),
        "secrets_error": raw.get("secrets", {}).get("error"),
    }

    return {"findings": findings, "stats": stats, "raw_results": raw}


def _to_finding(raw: dict[str, Any], repo_path: Path) -> dict[str, Any]:
    """Map a runner-shape dict to a garlicpress-shape Finding."""
    tool = raw.get("tool", "static")
    rule_id = raw.get("rule_id", "")
    file_path = raw.get("file", "")
    line = raw.get("line", 0)
    col = raw.get("column", 0)

    # Make the location relative to the repo root if possible — easier on a
    # human reader. Use ``repo_path / file_path`` rather than a plain
    # ``Path(file_path).resolve()`` so a tool that emits a path relative to
    # the repo (rather than absolute) doesn't get joined to the process cwd.
    # The ``/`` operator preserves an absolute file_path as-is.
    try:
        rel_path = str((repo_path / file_path).resolve().relative_to(repo_path))
    except (ValueError, OSError):
        rel_path = file_path or "<unknown>"

    seed = f"static:{tool}:{rule_id}:{rel_path}:{line}:{col}:{raw.get('message','')}"
    finding_id = "static_" + hashlib.sha1(seed.encode()).hexdigest()[:12]

    description = f"{tool}/{rule_id}: {raw.get('message','').strip()[:300]}" if rule_id else raw.get("message", "")
    evidence_parts = [f"{tool} rule {rule_id}" if rule_id else tool]
    if raw.get("url"):
        evidence_parts.append(f"docs: {raw['url']}")
    extra = raw.get("extra") or {}
    if extra.get("fix_versions"):
        evidence_parts.append(f"fix: upgrade to {', '.join(extra['fix_versions'])}")
    if extra.get("aliases"):
        evidence_parts.append(f"aliases: {', '.join(extra['aliases'])}")
    if extra.get("is_verified"):
        evidence_parts.append("VERIFIED LIVE secret (detect-secrets confirmed)")
    evidence = "\n".join(evidence_parts)

    static_meta: dict[str, Any] = {
        "tool": tool,
        "rule_id": rule_id,
        "column": col,
        "url": raw.get("url", ""),
    }
    if extra:
        static_meta["extra"] = extra

    return {
        "finding_id": finding_id,
        "severity": raw.get("severity", "low"),
        "type": raw.get("type", "other"),
        "location": f"{rel_path}:{line}",
        "description": description,
        "evidence": evidence,
        "traceability": {"file": rel_path, "line": line, "git_sha": None},
        "static_meta": static_meta,
    }


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop exact duplicates by (location, tool, rule_id).

    Doesn't merge ruff vs semgrep findings on the same line — same bug surfaced
    by two tools is signal, not noise, and the rule_ids will differ anyway.
    """
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for f in findings:
        key = (f["location"], f.get("static_meta", {}).get("tool"), f.get("static_meta", {}).get("rule_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _severity_sort_key(f: dict[str, Any]) -> tuple:
    return (_SEVERITY_ORDER.get(f.get("severity", "low"), 99), f.get("location", ""))


def _empty_result() -> dict[str, Any]:
    return {
        "findings": [],
        "stats": {
            "total_findings": 0,
            "ruff_count": 0,
            "semgrep_count": 0,
            "mypy_count": 0,
            "pip_audit_count": 0,
            "secrets_count": 0,
            "ruff_duration_seconds": 0.0,
            "semgrep_duration_seconds": 0.0,
            "mypy_duration_seconds": 0.0,
            "pip_audit_duration_seconds": 0.0,
            "secrets_duration_seconds": 0.0,
            "ruff_error": None,
            "semgrep_error": None,
            "mypy_error": None,
            "pip_audit_error": None,
            "secrets_error": None,
        },
        "raw_results": {},
    }
