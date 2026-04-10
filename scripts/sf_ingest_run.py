#!/usr/bin/env python3
"""
sf_ingest_run.py — one-shot bulk ingest of Salesforce Cases into the local
RAG store. Designed to be safe to run multiple times: state file tracks
already-completed case IDs so a crash mid-run resumes where it left off.

Usage (from inside the beigebox container — that's where BBClient can reach
host.docker.internal:9009):

    docker exec beigebox python3 /app/scripts/sf_ingest_run.py [--cutoff YYYY-MM-DD]

Defaults:
    --cutoff 2024-01-01      pull every case created on or after this date
    --views All_Open_Cases_SXPortal,All_Closed_Cases_SXPortal
    --rate-limit 0.20        sleep this many seconds between fetch_case calls (5 req/s ceiling)
    --disk-floor-gb 3        abort if disk free drops below this many GiB
    --state ~/.beigebox/sf_ingest_state.json   resume state file
    --log /app/logs/sf_ingest.log              progress log (tail -f friendly)

Read-only against Salesforce. ONLY uses Aura `getRecordWithFields`,
`postListRecordsByName`, `postRelatedListRecords`, and `getCompactFeedModel`.
NEVER mutates customer data — see ~/.claude/.../memory/feedback_customer_data_readonly.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow execution either as `python /app/scripts/sf_ingest_run.py` or
# `python -m scripts.sf_ingest_run` — both should find the beigebox package.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))

from beigebox.tools.sf_ingest import SfIngestTool, LIST_VIEWS  # noqa: E402


# ── State / log helpers ──────────────────────────────────────────────────────


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"completed": [], "started_at": None, "last_run": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"completed": [], "started_at": None, "last_run": None}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def disk_free_gib(path: str) -> float:
    s = shutil.disk_usage(path)
    return s.free / (1024 ** 3)


def setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sf_ingest_run")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


# ── Main ─────────────────────────────────────────────────────────────────────


async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cutoff", default="2024-01-01", help="ISO date; cases on or after this date are pulled")
    p.add_argument("--views", default=",".join(LIST_VIEWS),
                   help="Comma-separated SF list view API names to walk")
    p.add_argument("--rate-limit", type=float, default=0.20,
                   help="Seconds to sleep between fetch_case calls")
    p.add_argument("--disk-floor-gb", type=float, default=3.0,
                   help="Abort if free disk drops below this many GiB")
    p.add_argument("--state", default="/app/data/sf_ingest_state.json",
                   help="Resume state file path (inside container — needs to be appuser-writable)")
    p.add_argument("--log", default="/app/logs/sf_ingest.log",
                   help="Log file (tail -f friendly)")
    p.add_argument("--out-dir", default="/app/workspace/out/rag/SF",
                   help="Markdown output directory")
    p.add_argument("--ws-url", default="ws://host.docker.internal:9009",
                   help="BrowserBox WebSocket URL")
    p.add_argument("--limit", type=int, default=0,
                   help="If >0, stop after this many cases (for dry runs)")
    p.add_argument("--dry-run", action="store_true",
                   help="Discover only — do not fetch_case or write_case")
    args = p.parse_args()

    log = setup_logging(Path(args.log))
    log.info("=" * 70)
    log.info("sf_ingest_run starting")
    log.info("  cutoff:       %s", args.cutoff)
    log.info("  views:        %s", args.views)
    log.info("  out_dir:      %s", args.out_dir)
    log.info("  rate_limit:   %.2f s/case", args.rate_limit)
    log.info("  disk_floor:   %.1f GiB", args.disk_floor_gb)
    log.info("  state file:   %s", args.state)
    log.info("  dry_run:      %s", args.dry_run)

    state_path = Path(args.state)
    state = load_state(state_path)
    completed: set[str] = set(state.get("completed", []))
    log.info("  resume state: %d already completed", len(completed))

    if not state.get("started_at"):
        state["started_at"] = datetime.now(timezone.utc).isoformat()

    # Disk floor check up front
    free = disk_free_gib(args.out_dir if Path(args.out_dir).exists() else "/")
    log.info("  disk free:    %.2f GiB", free)
    if free < args.disk_floor_gb:
        log.error("ABORT: free disk %.2f GiB below floor %.2f GiB", free, args.disk_floor_gb)
        sys.exit(2)

    # Graceful shutdown — save state on Ctrl+C
    stop = {"flag": False}

    def _sig_handler(signum, frame):
        log.warning("signal %d received — finishing current case then stopping", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    tool = SfIngestTool(ws_url=args.ws_url, timeout=60.0, out_dir=args.out_dir)

    # ── Discovery phase ──────────────────────────────────────────────────────
    views = [v.strip() for v in args.views.split(",") if v.strip()]
    log.info("discover_native(cutoff=%s, views=%s)", args.cutoff, views)
    t0 = time.monotonic()
    try:
        stubs = await tool.discover_native(cutoff=args.cutoff, views=views)
    except Exception as e:
        log.error("discover_native failed: %s: %s", type(e).__name__, e)
        sys.exit(3)
    log.info("discovered %d unique cases in %.1fs", len(stubs), time.monotonic() - t0)

    if args.dry_run:
        log.info("DRY RUN — exiting before fetch_case")
        log.info("  first 5 stubs: %s", json.dumps(stubs[:5], indent=2))
        return

    # ── Fetch + write loop ──────────────────────────────────────────────────
    pending = [s for s in stubs if s.get("id") and s["id"] not in completed]
    log.info("%d cases pending (skipping %d already in state)",
             len(pending), len(stubs) - len(pending))

    if args.limit > 0:
        pending = pending[: args.limit]
        log.info("--limit %d applied → processing %d cases", args.limit, len(pending))

    total = len(pending)
    success = 0
    fail = 0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 5

    for idx, stub in enumerate(pending, start=1):
        if stop["flag"]:
            log.warning("stop flag set — saving state and exiting")
            break

        case_id = stub["id"]
        case_num = stub.get("caseNumber", "?")

        # Disk floor mid-run check (every 25 cases)
        if idx % 25 == 0:
            free = disk_free_gib(args.out_dir)
            log.info("[%d/%d] disk free: %.2f GiB", idx, total, free)
            if free < args.disk_floor_gb:
                log.error("ABORT mid-run: free disk %.2f GiB below floor %.2f GiB",
                          free, args.disk_floor_gb)
                break

        try:
            t1 = time.monotonic()
            case = await tool.fetch_case(case_id)
            path = tool.write_case(case)
            elapsed = time.monotonic() - t1
            feed_n = len(case.get("feed") or [])
            jira_n = len(case.get("jira") or [])
            log.info("[%d/%d] %s ✓ %.1fs feed=%d jira=%d → %s",
                     idx, total, case_num, elapsed, feed_n, jira_n,
                     Path(path).name)
            completed.add(case_id)
            success += 1
            consecutive_failures = 0
        except Exception as e:
            fail += 1
            consecutive_failures += 1
            log.error("[%d/%d] %s ✗ %s: %s", idx, total, case_num, type(e).__name__, e)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error("ABORT: %d consecutive failures — stopping to avoid pounding the API",
                          consecutive_failures)
                break

        # Periodic state save (every 10 cases) so a crash doesn't lose progress
        if idx % 10 == 0:
            state["completed"] = sorted(completed)
            state["last_progress"] = {
                "idx": idx, "total": total, "success": success, "fail": fail,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            save_state(state_path, state)

        # Rate limit
        await asyncio.sleep(args.rate_limit)

    # Final state save
    state["completed"] = sorted(completed)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["last_summary"] = {
        "discovered": len(stubs),
        "pending":    total,
        "success":    success,
        "fail":       fail,
    }
    save_state(state_path, state)

    log.info("=" * 70)
    log.info("sf_ingest_run finished")
    log.info("  discovered: %d", len(stubs))
    log.info("  processed:  %d", total)
    log.info("  success:    %d", success)
    log.info("  failed:     %d", fail)
    log.info("  state:      %s", state_path)
    log.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
