"""CLI entrypoint for the fuzz skill.

Invoke as `python3 -m beigebox.skills.fuzz <repo> [opts]`,
or via the wrapper at `scripts/fuzz.sh`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .pipeline import run_fuzzing


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="beigebox-fuzz",
        description="Discover and fuzz Python functions in a repository.",
    )
    p.add_argument("repo", type=Path, help="Repository root to scan for Python functions.")
    p.add_argument(
        "--max-functions",
        type=int,
        default=25,
        help="Cap on number of functions to fuzz (highest risk first). Default: 25.",
    )
    p.add_argument(
        "--budget",
        type=int,
        default=120,
        help="Total fuzzing time budget across all functions, in seconds. Default: 120.",
    )
    p.add_argument(
        "--max-crashes-per-func",
        type=int,
        default=5,
        help="Stop fuzzing a function after this many distinct crashes. Default: 5.",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Number of fuzz harnesses to run in parallel. Default: 2.",
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
        run_fuzzing(
            args.repo,
            max_functions=args.max_functions,
            total_budget_seconds=args.budget,
            max_crashes_per_func=args.max_crashes_per_func,
            concurrency=args.concurrency,
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
    return 0


def _print_summary(result: dict) -> None:
    stats = result.get("stats", {})
    findings = result.get("findings", [])
    print("Fuzz run summary")
    print("-" * 60)
    print(f"  Functions discovered:  {stats.get('functions_discovered', 0)}")
    print(f"  Functions fuzzed:      {stats.get('functions_fuzzed', 0)}")
    print(f"  Total iterations:      {stats.get('total_iterations', 0)}")
    print(f"  Total duration (s):    {stats.get('total_duration_seconds', 0.0)}")
    print(f"  Crashes (raw):         {stats.get('crashes_raw', 0)}")
    print(f"  Crashes (classified):  {stats.get('crashes_classified', 0)}")
    print()
    if not findings:
        print("No findings.")
        return
    print(f"Findings ({len(findings)}):")
    for f in findings:
        print(f"  [{f['severity'].upper():<8}] {f['location']:<40}  {f['description']}")
