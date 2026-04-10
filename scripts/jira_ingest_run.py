#!/usr/bin/env python3
"""
jira_ingest_run.py — one-shot bulk ingest of Jira issues into the local RAG store.

Designed to be safe to run multiple times: state file tracks already-completed
issue keys so a crash mid-run resumes where it left off.

Usage (from inside the beigebox container, where the env vars are set):

    docker exec beigebox python3 /app/scripts/jira_ingest_run.py [--jql 'project = SX'] [--limit N] [--dry-run]

Defaults:
    --jql "project = SX AND created >= 2020-01-01"
    --rate-limit 0.10        sleep this many seconds between issue fetches (10 req/s ceiling)
    --disk-floor-gb 3        abort if disk free drops below this many GiB
    --hard-cap 50000         abort discovery if approximate count exceeds this
    --state /app/data/jira_ingest_state.json
    --log /app/logs/jira_ingest.log
    --out-dir /app/workspace/out/rag/JIRA

Read-only against Atlassian. Uses ONLY:
    POST /rest/api/3/search/jql       (paginated discovery via nextPageToken)
    GET  /rest/api/3/issue/{key}      (full issue + comments)
NEVER mutates customer data — see ~/.claude/.../memory/feedback_customer_data_readonly.md
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError


# ── Config from env ──────────────────────────────────────────────────────────


BASE_URL = os.environ.get("ATLASSIAN_BASE_URL", "").rstrip("/")
EMAIL    = os.environ.get("ATLASSIAN_EMAIL", "")
TOKEN    = os.environ.get("ATLASSIAN_API_TOKEN", "")

TIMEOUT_S = 20.0


def _auth_header() -> str:
    raw = f"{EMAIL}:{TOKEN}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _request(path: str, method: str = "GET", body: dict | None = None, params: dict | None = None) -> tuple[int, dict | str]:
    if not (BASE_URL and EMAIL and TOKEN):
        return (0, "Atlassian credentials missing — set ATLASSIAN_BASE_URL/EMAIL/API_TOKEN")
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/json",
        "Authorization": _auth_header(),
        "User-Agent": "beigebox-jira-ingest/1.0",
    }
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return (resp.status, json.loads(raw))
            except json.JSONDecodeError:
                return (resp.status, raw)
    except HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return (e.code, f"HTTP {e.code}: {err_body[:300]}")
    except URLError as e:
        return (0, f"network error: {e.reason}")


# ── ADF (Atlassian Document Format) → plain text ─────────────────────────────


def _adf_to_text(node) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(n) for n in node)
    if isinstance(node, dict):
        ntype = node.get("type", "")
        text  = node.get("text", "")
        children = _adf_to_text(node.get("content"))
        if ntype in ("paragraph", "heading", "listItem", "bulletList", "orderedList"):
            return (text + children + "\n") if (text or children) else ""
        if ntype == "hardBreak":
            return "\n"
        if ntype == "codeBlock":
            return f"\n```\n{children}\n```\n"
        if ntype == "table":
            return "\n" + children + "\n"
        if ntype == "tableRow":
            return children.strip() + "\n"
        if ntype in ("tableHeader", "tableCell"):
            return children.strip() + " | "
        return text + children
    return ""


# ── Filename helpers ─────────────────────────────────────────────────────────


_FILENAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename(s: str, max_len: int = 80) -> str:
    s = (s or "").strip()
    s = _FILENAME_BAD.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:max_len] if s else "untitled"


# ── Markdown writer ──────────────────────────────────────────────────────────


def write_issue(issue: dict, out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)

    key = issue.get("key", "?")
    f = issue.get("fields", {}) or {}

    summary  = f.get("summary", "") or ""
    status   = (f.get("status") or {}).get("name", "?")
    itype    = (f.get("issuetype") or {}).get("name", "?")
    priority = (f.get("priority") or {}).get("name", "?")
    assignee = ((f.get("assignee") or {}).get("displayName")) or "unassigned"
    reporter = ((f.get("reporter") or {}).get("displayName")) or "unknown"
    created  = (f.get("created") or "")[:19]
    updated  = (f.get("updated") or "")[:19]
    resolved = (f.get("resolutiondate") or "")[:19] if f.get("resolutiondate") else ""

    project_key = (f.get("project") or {}).get("key", "")
    url = f"{BASE_URL}/browse/{key}"

    desc = _adf_to_text(f.get("description")).strip()
    labels = f.get("labels") or []
    components = [c.get("name") for c in (f.get("components") or []) if c.get("name")]
    fix_versions = [v.get("name") for v in (f.get("fixVersions") or []) if v.get("name")]

    fname = f"{key} - {_sanitize_filename(summary)}.md"
    out_path = out_dir / fname

    lines: list[str] = [
        f"# {key}: {summary}",
        "",
        f"**URL:**       {url}",
        f"**Project:**   {project_key}",
        f"**Type:**      {itype}",
        f"**Status:**    {status}",
        f"**Priority:**  {priority}",
        f"**Assignee:**  {assignee}",
        f"**Reporter:**  {reporter}",
        f"**Created:**   {created}",
        f"**Updated:**   {updated}",
    ]
    if resolved:
        lines.append(f"**Resolved:**  {resolved}")
    if labels:
        lines.append(f"**Labels:**    {', '.join(labels)}")
    if components:
        lines.append(f"**Components:** {', '.join(components)}")
    if fix_versions:
        lines.append(f"**Fix Versions:** {', '.join(fix_versions)}")
    lines.append("")

    if desc:
        lines += ["## Description", "", desc, ""]

    comments = ((f.get("comment") or {}).get("comments")) or []
    if comments:
        lines += [f"## Comments ({len(comments)})", ""]
        for c in comments:
            author = (c.get("author") or {}).get("displayName", "?")
            ctime  = (c.get("created") or "")[:19]
            body   = _adf_to_text(c.get("body")).strip()
            lines += [f"### {ctime} — {author}", "", body, ""]

    lines.append(f"_Ingested: {datetime.now(timezone.utc).isoformat()}_")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


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
    logger = logging.getLogger("jira_ingest_run")
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


# ── Discovery: paginated /search/jql ─────────────────────────────────────────


def discover_keys(jql: str, log: logging.Logger, hard_cap: int) -> list[str]:
    """Walk /rest/api/3/search/jql with nextPageToken, collect issue keys."""
    # First, an approximate count for sanity
    status, count_data = _request(
        "/rest/api/3/search/approximate-count",
        method="POST",
        body={"jql": jql},
    )
    if status == 200 and isinstance(count_data, dict):
        approx = count_data.get("count", 0)
        log.info("approximate issue count for jql: %d", approx)
        if approx > hard_cap:
            log.error("ABORT: %d > hard cap %d. Refine JQL or raise --hard-cap.", approx, hard_cap)
            sys.exit(2)

    keys: list[str] = []
    next_token: str | None = None
    page = 0
    while True:
        page += 1
        body = {
            "jql": jql,
            "fields": ["summary"],     # we just need keys here; full fetch comes later
            "maxResults": 100,
        }
        if next_token:
            body["nextPageToken"] = next_token
        status, data = _request("/rest/api/3/search/jql", method="POST", body=body)
        if status != 200 or not isinstance(data, dict):
            log.error("discover failed at page %d: %s", page, data)
            break
        issues = data.get("issues", [])
        for it in issues:
            k = it.get("key")
            if k:
                keys.append(k)
        next_token = data.get("nextPageToken")
        log.info("discover page %d: %d issues (cumulative %d, more=%s)",
                 page, len(issues), len(keys), bool(next_token))
        if not next_token:
            break
        if page > 600:  # 60k issues max — safety net
            log.warning("page cap hit (%d) — stopping discovery early", page)
            break
    return keys


# ── Per-issue fetch ──────────────────────────────────────────────────────────


def fetch_issue(key: str) -> tuple[int, dict | str]:
    return _request(
        f"/rest/api/3/issue/{urllib.parse.quote(key)}",
        params={"fields": "summary,status,assignee,reporter,priority,issuetype,project,created,updated,resolutiondate,description,comment,labels,components,fixVersions"},
    )


# ── Main ─────────────────────────────────────────────────────────────────────


async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jql", default="project = SX AND created >= 2020-01-01",
                   help="JQL query to scope the ingest")
    p.add_argument("--rate-limit", type=float, default=0.10,
                   help="Seconds to sleep between fetch_issue calls")
    p.add_argument("--disk-floor-gb", type=float, default=3.0,
                   help="Abort if free disk drops below this many GiB")
    p.add_argument("--hard-cap", type=int, default=50000,
                   help="Refuse to start if approximate-count exceeds this")
    p.add_argument("--state", default="/app/data/jira_ingest_state.json")
    p.add_argument("--log", default="/app/logs/jira_ingest.log")
    p.add_argument("--out-dir", default="/app/workspace/out/rag/JIRA")
    p.add_argument("--limit", type=int, default=0,
                   help="If >0, stop after this many issues (for dry runs)")
    p.add_argument("--dry-run", action="store_true",
                   help="Discover keys only — do not fetch issue bodies")
    args = p.parse_args()

    log = setup_logging(Path(args.log))
    log.info("=" * 70)
    log.info("jira_ingest_run starting")
    log.info("  jql:          %s", args.jql)
    log.info("  out_dir:      %s", args.out_dir)
    log.info("  rate_limit:   %.2f s/issue", args.rate_limit)
    log.info("  disk_floor:   %.1f GiB", args.disk_floor_gb)
    log.info("  hard_cap:     %d", args.hard_cap)
    log.info("  state file:   %s", args.state)
    log.info("  dry_run:      %s", args.dry_run)

    if not (BASE_URL and EMAIL and TOKEN):
        log.error("missing Atlassian env vars — cannot continue")
        sys.exit(1)

    state_path = Path(args.state)
    state = load_state(state_path)
    completed: set[str] = set(state.get("completed", []))
    log.info("  resume state: %d already completed", len(completed))

    free = disk_free_gib(args.out_dir if Path(args.out_dir).exists() else "/")
    log.info("  disk free:    %.2f GiB", free)
    if free < args.disk_floor_gb:
        log.error("ABORT: free disk %.2f GiB below floor %.2f GiB", free, args.disk_floor_gb)
        sys.exit(2)

    stop = {"flag": False}

    def _sig_handler(signum, frame):
        log.warning("signal %d — finishing current issue then stopping", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    # ── Discovery ────────────────────────────────────────────────────────────
    log.info("discover_keys starting")
    t0 = time.monotonic()
    keys = discover_keys(args.jql, log, args.hard_cap)
    log.info("discovered %d keys in %.1fs", len(keys), time.monotonic() - t0)

    if args.dry_run:
        log.info("DRY RUN — first 10 keys: %s", keys[:10])
        return

    pending = [k for k in keys if k not in completed]
    log.info("%d pending (skipping %d already in state)", len(pending), len(keys) - len(pending))
    if args.limit > 0:
        pending = pending[: args.limit]
        log.info("--limit %d applied → processing %d", args.limit, len(pending))

    out_dir = Path(args.out_dir)
    total = len(pending)
    success = 0
    fail = 0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 5

    for idx, key in enumerate(pending, start=1):
        if stop["flag"]:
            log.warning("stop flag — saving state and exiting")
            break

        if idx % 25 == 0:
            free = disk_free_gib(args.out_dir)
            log.info("[%d/%d] disk free: %.2f GiB", idx, total, free)
            if free < args.disk_floor_gb:
                log.error("ABORT mid-run: disk %.2f GiB < floor %.2f GiB", free, args.disk_floor_gb)
                break

        t1 = time.monotonic()
        status, issue = fetch_issue(key)
        if status != 200 or not isinstance(issue, dict):
            fail += 1
            consecutive_failures += 1
            log.error("[%d/%d] %s ✗ HTTP %d: %s", idx, total, key, status, str(issue)[:200])
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error("ABORT: %d consecutive failures", consecutive_failures)
                break
            await asyncio.sleep(args.rate_limit)
            continue

        try:
            path = write_issue(issue, out_dir)
            elapsed = time.monotonic() - t1
            comments_n = len(((issue.get("fields") or {}).get("comment") or {}).get("comments") or [])
            log.info("[%d/%d] %s ✓ %.2fs comments=%d → %s",
                     idx, total, key, elapsed, comments_n, Path(path).name)
            completed.add(key)
            success += 1
            consecutive_failures = 0
        except Exception as e:
            fail += 1
            consecutive_failures += 1
            log.error("[%d/%d] %s ✗ write failed: %s", idx, total, key, e)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error("ABORT: %d consecutive failures", consecutive_failures)
                break

        if idx % 25 == 0:
            state["completed"] = sorted(completed)
            state["last_progress"] = {
                "idx": idx, "total": total, "success": success, "fail": fail,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            save_state(state_path, state)

        await asyncio.sleep(args.rate_limit)

    state["completed"] = sorted(completed)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["last_summary"] = {
        "discovered": len(keys),
        "pending":    total,
        "success":    success,
        "fail":       fail,
    }
    save_state(state_path, state)

    log.info("=" * 70)
    log.info("jira_ingest_run finished")
    log.info("  discovered: %d", len(keys))
    log.info("  processed:  %d", total)
    log.info("  success:    %d", success)
    log.info("  failed:     %d", fail)
    log.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
