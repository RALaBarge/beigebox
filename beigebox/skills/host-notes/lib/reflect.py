"""`host-notes reflect`: parse transcript, run reflection per detected host, write.

Modes:
- explicit: `reflect <host>` reads transcript from --transcript or stdin
- auto: `reflect --auto` reads $CLAUDE_CODE_TRANSCRIPT (file path), detects hosts,
        runs reflection per detected host. Used by the PreCompact hook.

Behavior:
- BeigeBox health-check first (2s timeout). If down, exit 0 silently so we
  never block compaction.
- Per host: read existing notes, build prompt from prompts/reflect.txt,
  call BeigeBox, parse JSON array, sanity-filter, append under flock.
- Counter increment is INSIDE the same flock as the append. On dedup-trigger
  failure, counter resets to 1.
- Stderr goes to <project>/beigebox/host-notes/.reflect.log (rotated at 1MB).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import common  # type: ignore[import-not-found]

DEDUP_EVERY_N = 5
LOG_ROTATE_BYTES = 1_000_000
PROMPT_VERSION = 1


def _log(msg: str) -> None:
    log_path = common.notes_root() / ".reflect.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and log_path.stat().st_size > LOG_ROTATE_BYTES:
        log_path.rename(log_path.with_suffix(".log.1"))
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")


def _read_transcript(args) -> str:
    if args.transcript:
        return Path(args.transcript).read_text(encoding="utf-8", errors="replace")
    env_path = os.environ.get("CLAUDE_CODE_TRANSCRIPT")
    if env_path and Path(env_path).exists():
        return Path(env_path).read_text(encoding="utf-8", errors="replace")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _build_prompt(template: str, host_key: str, existing: str, transcript: str) -> str:
    return (
        template
        .replace("{{HOST}}", host_key)
        .replace("{{EXISTING_NOTES}}", existing.strip() or "(none yet)")
        .replace("{{TRANSCRIPT}}", transcript)
    )


def _parse_response(raw: str) -> list[dict]:
    """Strip optional markdown fences, parse JSON array. Return [] on failure."""
    s = raw.strip()
    if s.startswith("```"):
        # Strip leading fence
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.endswith("```"):
            s = s[: -3]
    s = s.strip()
    if not s.startswith("["):
        return []
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, list):
        return []
    out = []
    for item in obj:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        conf = str(item.get("confidence", "")).strip().lower()
        if not fact or len(fact) > 200:
            continue
        if len(evidence) > 80:
            evidence = evidence[:77] + "..."
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        # Defensive secret-shape filter (prompt-side rejection is primary).
        if _looks_like_secret(fact) or _looks_like_secret(evidence):
            continue
        out.append({"fact": fact, "evidence": evidence, "confidence": conf})
    return out


# Conservative: catches likely secret VALUES, not the literal word "password".
import re as _re
_SECRET_RE = _re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|bearer)\s*[:=]\s*['\"]?[A-Za-z0-9!@#$%^&*_+=/-]{6,}"
)


def _looks_like_secret(s: str) -> bool:
    return bool(_SECRET_RE.search(s))


def _format_bullet(item: dict, today: date) -> str:
    return f"{today.isoformat()} [{item['confidence']}]: {item['fact']} — {item['evidence']}"


def _counter_path(host_key: str) -> Path:
    return common.host_dir(host_key) / ".write-count"


def _read_counter(host_key: str) -> int:
    p = _counter_path(host_key)
    if not p.exists():
        return 0
    try:
        return int(p.read_text().strip() or "0")
    except ValueError:
        return 0


def _write_counter(host_key: str, value: int) -> None:
    _counter_path(host_key).write_text(str(value))


def reflect_for_host(host_key: str, transcript: str, prompt_template: str) -> int:
    cfg = common.load_config()
    host = cfg.by_key(host_key)
    if host is None:
        _log(f"reflect: unknown host {host_key!r}, skipping")
        return 0
    if host.forbidden:
        _log(f"reflect: forbidden host {host_key!r}, skipping")
        return 0

    existing = common.read_notes(host_key)
    user_msg = _build_prompt(prompt_template, host_key, existing, transcript)
    system = (
        "You extract durable operator knowledge from coding-agent transcripts. "
        "Output strict JSON arrays only. No prose, no fences, no apology."
    )

    try:
        raw = common.call_chat(system, user_msg)
    except Exception as e:
        _log(f"reflect[{host_key}]: chat call failed: {type(e).__name__}: {e}")
        return 0

    items = _parse_response(raw)
    if not items:
        _log(f"reflect[{host_key}]: 0 items extracted")
        return 0

    today = date.today()
    bullets = [_format_bullet(it, today) for it in items]

    with common.HostLock(host_key):
        common.ensure_header(host_key, PROMPT_VERSION)
        # Counter increment is INSIDE the lock.
        counter = _read_counter(host_key) + 1

        # Dedup BEFORE append if we hit the threshold (Grok fix: was after,
        # which let us cross the cap before consolidation).
        if counter >= DEDUP_EVERY_N:
            try:
                from dedup import dedup_for_host  # type: ignore[import-not-found]
                ok = dedup_for_host(host_key, locked=True)
                if ok:
                    counter = 0  # reset on success
                else:
                    counter = 1  # retry next time, don't loop forever this round
            except Exception as e:
                _log(f"reflect[{host_key}]: dedup pre-append failed: {e}")
                counter = 1

        appended = common.append_bullets(host_key, bullets)
        _write_counter(host_key, counter)

    _log(f"reflect[{host_key}]: appended {appended} (counter={counter})")
    return appended


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="host-notes reflect")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("host", nargs="?", help="canonical_key to reflect for")
    g.add_argument("--auto", action="store_true", help="auto-detect hosts from transcript")
    p.add_argument("--transcript", help="path to transcript file (overrides env/stdin)")
    args = p.parse_args(argv)

    if not common.beigebox_health_ok():
        _log("reflect: BeigeBox down, exiting 0")
        return 0

    template = (common.skill_root() / "prompts" / "reflect.txt").read_text()
    transcript = _read_transcript(args)
    if not transcript.strip():
        _log("reflect: empty transcript")
        return 0

    cfg = common.load_config()
    if cfg.is_forbidden_text(transcript):
        _log("reflect: forbidden marker in transcript, skipping all")
        return 0

    if args.auto:
        hosts = common.detect_hosts(transcript, cfg)
        if not hosts:
            _log("reflect --auto: no hosts detected")
            return 0
        for h in hosts:
            try:
                reflect_for_host(h.canonical_key, transcript, template)
            except Exception as e:
                _log(f"reflect[{h.canonical_key}]: unhandled: {type(e).__name__}: {e}")
        return 0
    else:
        try:
            reflect_for_host(args.host, transcript, template)
        except Exception as e:
            _log(f"reflect[{args.host}]: unhandled: {type(e).__name__}: {e}")
            return 1
        return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import common  # noqa: F811
    raise SystemExit(main())
