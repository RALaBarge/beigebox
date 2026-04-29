---
name: host-notes
version: 1
description: Use to load or update accumulated per-host operator knowledge for the BeigeBox project's LAN hosts (pop-os, dssh/debian, assh/mac). Auto-loads at session start when the user references a known host. Auto-reflects at compaction. Notes capture non-obvious, transcript-validated facts (sudo quirks, container IPs, undocumented service contracts) — never secrets, never anything derivable from man/--help.
---

# host-notes

Per-host accumulated knowledge in `beigebox/host-notes/<canonical_key>/notes.md`. Filled retrospectively at compaction time, loaded on demand at session start.

The store is **gitignored** — these are personal observations about *your* machines, not project documentation. CLAUDE.md remains the curated, human-edited source of truth; host-notes is the agent's working memory of what worked.

## When to invoke

- **You don't.** It runs automatically via two hooks (see below).
- The exception: if you realize mid-session that you're working on a host and the auto-load didn't fire, run `scripts/load.sh <host>` manually.

## Architecture

| File | Purpose |
|------|---------|
| `hosts.json` | Host registry with markers, primary names, forbidden flags. Validated on every load. |
| `lib/common.py` | Config loader, host detection, flock, BeigeBox client. |
| `lib/load.py` | Emit notes for one host or all detected hosts. |
| `lib/reflect.py` | Run reflection prompt against transcript, parse JSON, append under flock. |
| `lib/dedup.py` | Semantic dedup + 200-line cap; runs *before* append every Nth time. |
| `lib/status.py` | Per-host size, bullet count, last update. |
| `prompts/reflect.txt` | The reflection prompt. PROMPT_VERSION embedded in notes.md frontmatter. |
| `prompts/dedup.txt` | The dedup prompt. |
| `scripts/*.sh` | Thin wrappers around the Python files. |

## Hooks (configured in `.claude/settings.json`)

```json
{
  "hooks": {
    "PreCompact": [
      {
        "matcher": "*",
        "hooks": [{
          "type": "command",
          "command": "${CLAUDE_PROJECT_DIR}/beigebox/skills/host-notes/scripts/reflect.sh --auto",
          "timeout": 45
        }]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [{
          "type": "command",
          "command": "${CLAUDE_PROJECT_DIR}/beigebox/skills/host-notes/scripts/load.sh --detect \"$CLAUDE_USER_PROMPT\" --once-per-session",
          "timeout": 5
        }]
      }
    ]
  }
}
```

The `UserPromptSubmit` hook prints any matching host's notes to the agent context. The `PreCompact` hook captures durable knowledge before compaction discards it.

## CLI

```bash
# Manually load notes for a host (useful if auto-load missed)
scripts/load.sh dssh
scripts/load.sh --detect "let's check what's running on debian"

# Reflect on a specific transcript file
scripts/reflect.sh dssh --transcript /path/to/transcript.jsonl

# Reflect from $CLAUDE_CODE_TRANSCRIPT, auto-detecting hosts (PreCompact mode)
scripts/reflect.sh --auto

# Force a dedup pass
scripts/dedup.sh dssh
scripts/dedup.sh --all

# Status across all hosts
scripts/status.sh
```

## Filters (the load-bearing rule)

Reflection only writes facts that pass **all three**:
1. **Non-obvious** — not derivable from `man`, `--help`, or one minute of poking
2. **Moved the task forward** — actually used in the transcript to unblock the agent
3. **Not already known** — not in CLAUDE.md or current notes.md

This is what stops the file from becoming a 4MB graveyard of stale truths.

## Forbidden hosts

`hssh` (webspace host) and `wssh` (Whatbox seedbox) are forbidden globally — listed in `hosts.json` `forbidden_global_patterns`. If a transcript contains any of those markers, **all** reflection for that transcript is aborted (defense-in-depth: the prompt also enforces this, the loader refuses, the writer refuses).

## Failure modes addressed

- **Counter rollback** — on dedup failure the counter resets to 1 inside the same flock as the write, so a hung dedup never permanently skews the schedule
- **Atomic dedup-before-append** — single pass that handles both semantic dedup and the 200-line cap; can't grow past cap before consolidation
- **Secret filter** — primary defense in the prompt itself; secondary regex filter on the parsed output rejects only secret-shaped values, not the literal word "password"
- **`--auto` mode spec** — reads `$CLAUDE_CODE_TRANSCRIPT` (PreCompact env var), falls back to `--transcript <path>`, falls back to stdin
- **Sentinel responses from `load.sh`** — `---HOST_NOTES_LOADED---`, `---HOST_NOTES_EMPTY---`, `---HOST_UNKNOWN---`, `---HOST_FORBIDDEN---` so the agent can distinguish "no notes yet" from "host doesn't exist"
- **Race protection** — flock per-host with 10s timeout; counter increment inside the same lock as the write
- **Unbounded log** — `.reflect.log` rotates at 1MB
- **BeigeBox down** — health-check before any call; exit 0 so compaction is never blocked
- **YAML frontmatter** — preserved across dedup; bullet-only operations leave header intact
- **Validation** — `hosts.json` validated on every load (canonical_key shape, regex compilability, marker dupes warned, required fields)

## Limitations

- **Tailscale IPs change** — markers should be updated in `hosts.json` when they do; consider replacing IP markers with hostname markers if Tailscale gets rotated. There is no auto-migration.
- **Transcript spanning multiple hosts** — handled, but reflection runs per-host so the LLM sees the full transcript multiple times. Could be optimized to chunk per-host first if it ever matters.
- **Single-machine assumption** — the gitignored notes store is per-machine. Two machines reflecting on the same host won't sync; that's intentional for v1.
- **No automated tests yet** — the three filters and the secret regex would benefit from unit tests; structure is testable (pure functions in `_parse_response`, `_looks_like_secret`).
