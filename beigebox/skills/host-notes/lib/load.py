"""`host-notes load <host|--detect>`: emit notes for a host to stdout.

Sentinels (always go to stdout for the calling agent to parse):
- "---HOST_NOTES_LOADED---" prefixes a successful load
- "---HOST_NOTES_EMPTY---"  if the host is known but has no notes yet
- "---HOST_UNKNOWN---"      if the host name doesn't match any registered host
- "---HOST_FORBIDDEN---"    if the host is marked forbidden in hosts.json
"""

from __future__ import annotations

import argparse
import sys

import common  # type: ignore[import-not-found]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="host-notes load")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("host", nargs="?", help="canonical_key or marker text to detect")
    g.add_argument(
        "--detect",
        metavar="TEXT",
        help="run detection over TEXT and emit all matching hosts' notes",
    )
    p.add_argument(
        "--once-per-session",
        action="store_true",
        help="if a /tmp token says we already loaded this host this session, exit silently",
    )
    args = p.parse_args(argv)

    cfg = common.load_config()

    targets = []
    if args.detect:
        if cfg.is_forbidden_text(args.detect):
            print("---HOST_FORBIDDEN---")
            return 0
        targets = common.detect_hosts(args.detect, cfg)
        if not targets:
            return 0
    else:
        h = cfg.by_key(args.host)
        if h is None:
            # Maybe the user passed a marker, try detection on that string.
            detected = common.detect_hosts(args.host, cfg)
            if detected:
                targets = detected
            else:
                print("---HOST_UNKNOWN---")
                return 0
        else:
            targets = [h]

    for host in targets:
        if host.forbidden:
            print(f"---HOST_FORBIDDEN--- {host.canonical_key}")
            continue
        if args.once_per_session:
            tok = common.load_token_path(host.canonical_key)
            if tok.exists():
                continue
            tok.touch()
        notes = common.read_notes(host.canonical_key)
        if not notes.strip():
            print(f"---HOST_NOTES_EMPTY--- {host.canonical_key}")
            continue
        print(f"---HOST_NOTES_LOADED--- {host.canonical_key}")
        print(notes)
        print(f"---HOST_NOTES_END--- {host.canonical_key}")
    return 0


if __name__ == "__main__":
    # Make `common` importable when invoked by absolute path.
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import common  # noqa: F811
    raise SystemExit(main())
