"""CLI entrypoint for the static skill.

Invoke as `python3 -m beigebox.skills.static <repo> [opts]`,
or via the wrapper at `scripts/static.sh`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .pipeline import (
    DEFAULT_RUFF_SELECT,
    DEFAULT_SEMGREP_CONFIG,
    run_static,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="beigebox-static",
        description="Static analysis (ruff + semgrep + mypy + pip-audit + detect-secrets) for a Python repository.",
    )
    p.add_argument("repo", type=Path, help="Repository root to scan.")
    p.add_argument(
        "--ruff-select",
        default=DEFAULT_RUFF_SELECT,
        help=f"Ruff rule selection. Default: {DEFAULT_RUFF_SELECT}",
    )
    p.add_argument(
        "--ruff-ignore",
        default=None,
        help="Ruff rules to ignore. Comma-separated.",
    )
    p.add_argument(
        "--semgrep-config",
        default=DEFAULT_SEMGREP_CONFIG,
        help=f"Semgrep config (registry shortcut, file, or comma-list). Default: {DEFAULT_SEMGREP_CONFIG}",
    )
    p.add_argument(
        "--mypy-strict", action="store_true",
        help="Run mypy in --strict mode (much louder; lots of low-severity findings).",
    )
    p.add_argument(
        "--mypy-follow-imports",
        choices=("normal", "silent", "skip", "error"),
        default="silent",
        help="Mypy --follow-imports value. Default: silent (don't recurse into untyped deps).",
    )
    p.add_argument(
        "--no-ruff", action="store_true", help="Disable the ruff runner.",
    )
    p.add_argument(
        "--no-semgrep", action="store_true", help="Disable the semgrep runner.",
    )
    p.add_argument(
        "--no-mypy", action="store_true", help="Disable the mypy runner.",
    )
    p.add_argument(
        "--no-pip-audit", action="store_true", help="Disable the pip-audit (dependency CVE) runner.",
    )
    p.add_argument(
        "--no-secrets", action="store_true", help="Disable the detect-secrets runner.",
    )
    p.add_argument(
        "--ruff-timeout", type=float, default=120.0, help="Ruff subprocess timeout (s). Default: 120.",
    )
    p.add_argument(
        "--semgrep-timeout", type=float, default=600.0, help="Semgrep subprocess timeout (s). Default: 600.",
    )
    p.add_argument(
        "--mypy-timeout", type=float, default=300.0, help="Mypy subprocess timeout (s). Default: 300.",
    )
    p.add_argument(
        "--pip-audit-timeout", type=float, default=180.0,
        help="pip-audit per-manifest timeout (s). Default: 180.",
    )
    p.add_argument(
        "--secrets-timeout", type=float, default=180.0,
        help="detect-secrets timeout (s). Default: 180.",
    )
    p.add_argument(
        "--format",
        choices=("json", "summary"),
        default="json",
        help="Output format. json: full machine-readable result. summary: human-readable.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON output to this file instead of stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.repo.is_dir():
        print(f"error: {args.repo} is not a directory", file=sys.stderr)
        return 2

    result = asyncio.run(
        run_static(
            args.repo,
            ruff_select=args.ruff_select,
            ruff_ignore=args.ruff_ignore,
            semgrep_config=args.semgrep_config,
            mypy_strict=args.mypy_strict,
            mypy_follow_imports=args.mypy_follow_imports,
            enable_ruff=not args.no_ruff,
            enable_semgrep=not args.no_semgrep,
            enable_mypy=not args.no_mypy,
            enable_pip_audit=not args.no_pip_audit,
            enable_secrets=not args.no_secrets,
            ruff_timeout=args.ruff_timeout,
            semgrep_timeout=args.semgrep_timeout,
            mypy_timeout=args.mypy_timeout,
            pip_audit_timeout=args.pip_audit_timeout,
            secrets_timeout=args.secrets_timeout,
        )
    )

    if args.out:
        args.out.write_text(json.dumps(result, indent=2))

    if args.format == "json":
        if not args.out:
            json.dump(result, sys.stdout, indent=2)
            sys.stdout.write("\n")
    else:
        _print_summary(result)

    # Non-zero if every enabled runner errored, OR if any high+ severity
    # finding surfaced.
    stats = result.get("stats", {})
    enabled_errors = []
    if not args.no_ruff:
        enabled_errors.append(stats.get("ruff_error"))
    if not args.no_semgrep:
        enabled_errors.append(stats.get("semgrep_error"))
    if not args.no_mypy:
        enabled_errors.append(stats.get("mypy_error"))
    if not args.no_pip_audit:
        enabled_errors.append(stats.get("pip_audit_error"))
    if not args.no_secrets:
        enabled_errors.append(stats.get("secrets_error"))
    if enabled_errors and all(e is not None for e in enabled_errors):
        return 3
    has_high = any(f.get("severity") in ("critical", "high") for f in result.get("findings", []))
    return 1 if has_high else 0


def _print_summary(result: dict) -> None:
    stats = result.get("stats", {})
    findings = result.get("findings", [])
    print("Static analysis summary")
    print("-" * 60)
    print(f"  ruff:           findings={stats.get('ruff_count', 0):<5} duration={stats.get('ruff_duration_seconds', 0):.2f}s  error={stats.get('ruff_error') or '-'}")
    print(f"  semgrep:        findings={stats.get('semgrep_count', 0):<5} duration={stats.get('semgrep_duration_seconds', 0):.2f}s  error={stats.get('semgrep_error') or '-'}")
    print(f"  mypy:           findings={stats.get('mypy_count', 0):<5} duration={stats.get('mypy_duration_seconds', 0):.2f}s  error={stats.get('mypy_error') or '-'}")
    print(f"  pip-audit:      findings={stats.get('pip_audit_count', 0):<5} duration={stats.get('pip_audit_duration_seconds', 0):.2f}s  error={stats.get('pip_audit_error') or '-'}")
    print(f"  detect-secrets: findings={stats.get('secrets_count', 0):<5} duration={stats.get('secrets_duration_seconds', 0):.2f}s  error={stats.get('secrets_error') or '-'}")
    print(f"  total findings (deduped): {stats.get('total_findings', 0)}")
    print()
    if not findings:
        print("No findings.")
        return
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    print("Severity breakdown:")
    for sev in ("critical", "high", "medium", "low"):
        if sev in by_sev:
            print(f"  {sev:<8} {by_sev[sev]}")
    print()
    print(f"Findings ({len(findings)}):")
    for f in findings:
        print(f"  [{f['severity'].upper():<8}] {f['location']:<55}  {f['description'][:90]}")
