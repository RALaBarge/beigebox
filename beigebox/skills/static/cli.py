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
        description="Static analysis (ruff + semgrep) for a Python repository.",
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
        "--no-ruff", action="store_true", help="Disable the ruff runner.",
    )
    p.add_argument(
        "--no-semgrep", action="store_true", help="Disable the semgrep runner.",
    )
    p.add_argument(
        "--ruff-timeout", type=float, default=120.0, help="Ruff subprocess timeout (s). Default: 120.",
    )
    p.add_argument(
        "--semgrep-timeout", type=float, default=600.0, help="Semgrep subprocess timeout (s). Default: 600.",
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
            enable_ruff=not args.no_ruff,
            enable_semgrep=not args.no_semgrep,
            ruff_timeout=args.ruff_timeout,
            semgrep_timeout=args.semgrep_timeout,
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

    # Non-zero if either runner errored AND found nothing useful, OR if any
    # high+ severity finding surfaced.
    stats = result.get("stats", {})
    if stats.get("ruff_error") and stats.get("semgrep_error"):
        return 3
    has_high = any(f.get("severity") in ("critical", "high") for f in result.get("findings", []))
    return 1 if has_high else 0


def _print_summary(result: dict) -> None:
    stats = result.get("stats", {})
    findings = result.get("findings", [])
    print("Static analysis summary")
    print("-" * 60)
    print(f"  ruff:    findings={stats.get('ruff_count', 0):<5} duration={stats.get('ruff_duration_seconds', 0):.2f}s  error={stats.get('ruff_error') or '-'}")
    print(f"  semgrep: findings={stats.get('semgrep_count', 0):<5} duration={stats.get('semgrep_duration_seconds', 0):.2f}s  error={stats.get('semgrep_error') or '-'}")
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
