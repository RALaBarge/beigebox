"""bb_orient — return the canonical project orientation file.

This is the dumb-on-purpose version. No synthesis, no scanning, no caching.
Just reads `beigebox/orientation.md` and returns it. Anything else lives in
the markdown file, which is the single source of truth.

Update orientation.md by hand when something changes (`git diff` will show
exactly what shifted). No cron, no git hook, no auto-regeneration.
"""

from __future__ import annotations

from pathlib import Path

ORIENTATION_PATH = Path(__file__).resolve().parent.parent / "orientation.md"


def bb_orient() -> str:
    """Return the project orientation document.

    Returns the markdown contents verbatim with a `Last updated:` mtime
    prepended so callers can tell when the source was last touched.
    """
    if not ORIENTATION_PATH.exists():
        return f"(orientation.md missing at {ORIENTATION_PATH})"
    mtime = ORIENTATION_PATH.stat().st_mtime
    from datetime import datetime
    stamp = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    body = ORIENTATION_PATH.read_text(encoding="utf-8")
    return f"_Last updated: {stamp}_\n\n{body}"


if __name__ == "__main__":
    print(bb_orient())
