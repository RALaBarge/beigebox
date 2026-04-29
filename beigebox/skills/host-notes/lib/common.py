"""Shared utilities for the host-notes skill: config, host detection, flock.

This module is intentionally dependency-light — only stdlib + an optional
`openai` SDK at call time. Designed to be called from `python3 path/to/file.py`
(no module-import gymnastics with the hyphenated skill directory).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# Skill root resolution. Caller passes __file__; we walk up to the skill dir.
def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def project_root() -> Path:
    # beigebox/skills/host-notes/lib/common.py → walk up four levels
    return skill_root().parent.parent.parent


def notes_root() -> Path:
    return project_root() / "beigebox" / "host-notes"


def hosts_config_path() -> Path:
    return skill_root() / "hosts.json"


# ---------------------------------------------------------------------------
# Config loading + validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Host:
    canonical_key: str
    primary_marker: str
    ssh_target: str | None
    markers: tuple[re.Pattern, ...]
    forbidden: bool
    notes: str


@dataclass(frozen=True)
class HostsConfig:
    version: int
    forbidden_global_patterns: tuple[re.Pattern, ...]
    hosts: tuple[Host, ...]

    def by_key(self, key: str) -> Host | None:
        for h in self.hosts:
            if h.canonical_key == key:
                return h
        return None

    def is_forbidden_text(self, text: str) -> bool:
        return any(p.search(text) for p in self.forbidden_global_patterns)


def load_config() -> HostsConfig:
    path = hosts_config_path()
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or "hosts" not in raw or "version" not in raw:
        raise SystemExit(f"hosts.json malformed: missing version or hosts ({path})")

    seen_keys: set[str] = set()
    seen_markers: set[str] = set()
    hosts: list[Host] = []
    for h in raw["hosts"]:
        for required in ("canonical_key", "primary_marker", "markers", "forbidden"):
            if required not in h:
                raise SystemExit(f"hosts.json: host missing field {required!r}: {h}")
        key = h["canonical_key"]
        if key in seen_keys:
            raise SystemExit(f"hosts.json: duplicate canonical_key {key!r}")
        seen_keys.add(key)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", key):
            raise SystemExit(
                f"hosts.json: canonical_key {key!r} must be a-z0-9_- (filesystem-safe)"
            )
        compiled: list[re.Pattern] = []
        for m in h["markers"]:
            if m in seen_markers:
                # Markers can legitimately appear more than once if they map
                # to multiple hosts (e.g. localhost). We just warn.
                print(
                    f"warning: marker {m!r} appears in multiple hosts",
                    file=sys.stderr,
                )
            seen_markers.add(m)
            try:
                compiled.append(re.compile(m, re.IGNORECASE))
            except re.error as e:
                raise SystemExit(f"hosts.json: invalid regex {m!r}: {e}")
        hosts.append(
            Host(
                canonical_key=key,
                primary_marker=h["primary_marker"],
                ssh_target=h.get("ssh_target"),
                markers=tuple(compiled),
                forbidden=bool(h["forbidden"]),
                notes=h.get("notes", ""),
            )
        )

    forbidden_globals = tuple(
        re.compile(p, re.IGNORECASE) for p in raw.get("forbidden_global_patterns", [])
    )
    return HostsConfig(
        version=int(raw["version"]),
        forbidden_global_patterns=forbidden_globals,
        hosts=tuple(hosts),
    )


# ---------------------------------------------------------------------------
# Host detection in text
# ---------------------------------------------------------------------------

def detect_hosts(text: str, cfg: HostsConfig) -> list[Host]:
    """Return hosts referenced in `text`, in canonical_key order, deduped.

    A host is referenced if any of its markers matches AND the match is not
    inside a known noise context (we strip code-fence-marked markdown before
    matching to avoid 'ssh dssh' in a code example causing a false positive
    on a totally unrelated session).
    """
    stripped = _strip_code_fences(text)
    found: list[Host] = []
    for host in cfg.hosts:
        if host.forbidden:
            continue
        if any(p.search(stripped) for p in host.markers):
            found.append(host)
    return found


def _strip_code_fences(text: str) -> str:
    # Remove ```...``` code blocks. Lazy + non-overlapping.
    return re.sub(r"```[\s\S]*?```", "", text)


# ---------------------------------------------------------------------------
# Per-host directory + flock
# ---------------------------------------------------------------------------

def host_dir(canonical_key: str) -> Path:
    p = notes_root() / canonical_key
    p.mkdir(parents=True, exist_ok=True)
    return p


def notes_path(canonical_key: str) -> Path:
    return host_dir(canonical_key) / "notes.md"


class HostLock:
    """Filesystem flock over <host>/.lock with 10s timeout."""

    def __init__(self, canonical_key: str, timeout: float = 10.0) -> None:
        self.lock_path = host_dir(canonical_key) / ".lock"
        self.timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> "HostLock":
        self._fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise SystemExit(
                        f"host-notes: could not acquire lock on {self.lock_path} "
                        f"after {self.timeout}s"
                    )
                time.sleep(0.1)

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


# ---------------------------------------------------------------------------
# Per-session "load once" token (under /tmp)
# ---------------------------------------------------------------------------

def load_token_path(canonical_key: str) -> Path:
    sid = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get(
        "CLAUDE_CODE_SESSION_ID"
    ) or "no-session"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
    return Path("/tmp") / f"beigebox-host-notes-load-{safe}-{canonical_key}"


# ---------------------------------------------------------------------------
# BeigeBox client
# ---------------------------------------------------------------------------

def beigebox_url() -> str:
    return os.environ.get("BEIGEBOX_URL", "http://localhost:1337/v1")


def reflect_model() -> str:
    return os.environ.get("BEIGEBOX_REFLECT_MODEL", "x-ai/grok-4.1-fast")


def beigebox_health_ok(url: str | None = None, timeout: float = 2.0) -> bool:
    import urllib.request

    base = (url or beigebox_url()).rstrip("/")
    # Try /models first (OpenAI-compatible); fall back to /health.
    for suffix in ("/models", "/health"):
        try:
            req = urllib.request.Request(base + suffix)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 500:
                    return True
        except Exception:
            continue
    return False


def call_chat(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    timeout: float = 90.0,
) -> str:
    """Call BeigeBox /chat/completions and return assistant text. Raises on error."""
    import urllib.request
    import urllib.error

    url = beigebox_url().rstrip("/") + "/chat/completions"
    payload = {
        "model": model or reflect_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer dummy",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    obj = json.loads(body)
    return obj["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Notes file IO
# ---------------------------------------------------------------------------

NOTES_HEADER_TEMPLATE = """---
host: {host}
prompt_version: {prompt_version}
schema: 1
---

"""

NOTES_HARD_CAP_LINES = 200


def read_notes(canonical_key: str) -> str:
    p = notes_path(canonical_key)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def ensure_header(canonical_key: str, prompt_version: int) -> None:
    """Write a fresh header if the file is empty. Idempotent."""
    p = notes_path(canonical_key)
    existing = read_notes(canonical_key)
    if existing.strip():
        return
    p.write_text(
        NOTES_HEADER_TEMPLATE.format(host=canonical_key, prompt_version=prompt_version),
        encoding="utf-8",
    )


def append_bullets(canonical_key: str, bullets: list[str]) -> int:
    """Append bullets atomically. Returns count appended.

    If notes.md exceeds NOTES_HARD_CAP_LINES after append, FIFO-drops oldest
    bullet lines (preserving the YAML header) until cap is satisfied.
    """
    if not bullets:
        return 0
    p = notes_path(canonical_key)
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    new_content = existing + ("" if existing.endswith("\n") or not existing else "\n")
    for b in bullets:
        if not b.startswith("- "):
            b = "- " + b
        new_content += b.rstrip() + "\n"
    new_content = _enforce_cap(new_content)
    p.write_text(new_content, encoding="utf-8")
    return len(bullets)


def _enforce_cap(content: str) -> str:
    head, body = _split_header(content)
    bullet_lines = [l for l in body.splitlines() if l.startswith("- ")]
    other_lines = [l for l in body.splitlines() if not l.startswith("- ")]
    if len(bullet_lines) <= NOTES_HARD_CAP_LINES:
        return content
    # FIFO drop: keep newest by file order. Bullets are written newest-last,
    # so we drop from the front.
    drop = len(bullet_lines) - NOTES_HARD_CAP_LINES
    bullet_lines = bullet_lines[drop:]
    return head + "\n".join(other_lines + bullet_lines).rstrip() + "\n"


def _split_header(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        return "", content
    end = content.find("\n---\n", 4)
    if end < 0:
        return "", content
    return content[: end + 5], content[end + 5 :]
