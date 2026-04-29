"""`host-notes dedup <host|--all>`: consolidate notes.md via the dedup prompt.

Combines semantic dedup AND the 200-line cap into a single atomic pass that
runs *before* an append (per Grok's review).

Returns True from `dedup_for_host` on success, False on failure (so the
reflect counter logic can decide whether to reset).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import common  # type: ignore[import-not-found]


def dedup_for_host(host_key: str, locked: bool = False) -> bool:
    cfg = common.load_config()
    host = cfg.by_key(host_key)
    if host is None or host.forbidden:
        return False

    existing = common.read_notes(host_key)
    if not existing.strip():
        return True  # nothing to do, success

    # Pull bullet lines only; preserve header.
    head, body = _split_header(existing)
    bullet_lines = [l for l in body.splitlines() if l.startswith("- ")]
    if len(bullet_lines) < 5:
        return True  # not worth a roundtrip

    template = (common.skill_root() / "prompts" / "dedup.txt").read_text()
    user_msg = template.replace("{{INPUT}}", "\n".join(bullet_lines))
    system = "You are a precise text-deduplication tool. Output the cleaned bullet list and nothing else."

    try:
        raw = common.call_chat(system, user_msg, timeout=60.0)
    except Exception as e:
        _log_dedup(f"dedup[{host_key}]: chat failed: {e}")
        return False

    cleaned = _clean_response(raw)
    if not cleaned:
        return False
    new_bullets = [l for l in cleaned.splitlines() if l.startswith("- ")]
    if not new_bullets:
        return False
    if len(new_bullets) > 200:
        new_bullets = new_bullets[-200:]  # newest-last assumption

    new_content = (head if head else "") + "\n".join(new_bullets).rstrip() + "\n"

    notes_p = common.notes_path(host_key)
    if locked:
        notes_p.write_text(new_content, encoding="utf-8")
    else:
        with common.HostLock(host_key):
            notes_p.write_text(new_content, encoding="utf-8")
    _log_dedup(f"dedup[{host_key}]: {len(bullet_lines)} -> {len(new_bullets)} bullets")
    return True


def _split_header(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        return "", content
    end = content.find("\n---\n", 4)
    if end < 0:
        return "", content
    return content[: end + 5], content[end + 5 :]


def _clean_response(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


def _log_dedup(msg: str) -> None:
    log_path = common.notes_root() / ".reflect.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="host-notes dedup")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("host", nargs="?", help="canonical_key")
    g.add_argument("--all", action="store_true")
    args = p.parse_args(argv)

    if not common.beigebox_health_ok():
        print("BeigeBox down; cannot dedup.", file=sys.stderr)
        return 1

    cfg = common.load_config()
    targets = (
        [h for h in cfg.hosts if not h.forbidden]
        if args.all
        else [cfg.by_key(args.host)]
    )
    rc = 0
    for h in targets:
        if h is None:
            print(f"unknown host: {args.host}", file=sys.stderr)
            rc = 2
            continue
        ok = dedup_for_host(h.canonical_key)
        if not ok:
            rc = max(rc, 1)
    return rc


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import common  # noqa: F811
    raise SystemExit(main())
