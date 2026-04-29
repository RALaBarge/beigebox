"""`host-notes status`: per-host size, last update, write counter."""

from __future__ import annotations

import os
import sys
from datetime import datetime

import common  # type: ignore[import-not-found]


def main(argv: list[str] | None = None) -> int:
    cfg = common.load_config()
    print(f"host-notes config v{cfg.version}, BeigeBox {'UP' if common.beigebox_health_ok() else 'DOWN'}")
    print(f"notes root: {common.notes_root()}")
    print()
    print(f"{'HOST':<14} {'STATUS':<10} {'BULLETS':<8} {'BYTES':<8} {'COUNTER':<8} {'UPDATED':<20}")
    for h in cfg.hosts:
        status = "FORBIDDEN" if h.forbidden else "OK"
        p = common.notes_path(h.canonical_key)
        if not p.exists():
            print(f"{h.canonical_key:<14} {status:<10} {'-':<8} {'-':<8} {'-':<8} (no notes yet)")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        bullets = sum(1 for l in text.splitlines() if l.startswith("- "))
        size = p.stat().st_size
        ctr_p = common.host_dir(h.canonical_key) / ".write-count"
        ctr = ctr_p.read_text().strip() if ctr_p.exists() else "0"
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"{h.canonical_key:<14} {status:<10} {bullets:<8} {size:<8} {ctr:<8} {mtime}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import common  # noqa: F811
    raise SystemExit(main())
