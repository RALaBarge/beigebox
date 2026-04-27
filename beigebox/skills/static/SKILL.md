---
name: static
version: 1
description: Use when the user wants static analysis / SAST / lint-with-teeth on a Python codebase — finding security smells, logic bugs, obvious defects without running the code. Wraps ruff (full ruleset including the bandit-port S rules) plus semgrep (registry-backed pattern + dataflow rules) and emits garlicpress-shape findings so the output merges with fuzz/static pipelines without translation. Two tools, not three: ruff's S ruleset already covers what bandit would.
---

# static

`scripts/static.sh` (or `python3 -m beigebox.skills.static`) — run ruff + semgrep against a Python repo and emit a single ranked finding list. Companion to the `fuzz` skill: same garlicpress-shape output, same async pipeline, callable from a Trinity orchestrator alongside fuzz.

## When to invoke

- User asks for "static analysis", "SAST", "find bugs without running", "lint with teeth"
- An audit / review needs the static side of a static-plus-fuzz sweep
- Catching things `fuzz` can't reach: dead code, unused imports, deprecated APIs, type-confused branches that never run with random inputs

## Why these two tools

`ruff` is the bandit successor for Python — its `S` ruleset is a port of bandit (`exec`, `eval`, `pickle`, weak crypto, `subprocess(shell=True)`, etc.) and runs in milliseconds. Adding `bandit` separately would mostly re-find the same things.

`semgrep` covers ground ruff can't: cross-file dataflow, framework-specific patterns (Django, Flask, requests), and a curated registry of security/correctness rule packs. Slower, but reaches deeper.

So the pair is **ruff (fast, AST-pattern, bandit-equivalent)** + **semgrep (slower, dataflow, registry)**. Skip bandit; ruff covers it.

## Usage

```bash
# default: ruff (S,F,E9,B,ASYNC,ARG,RUF,PL,TRY) + semgrep (p/python pack)
scripts/static.sh /path/to/repo

# focus on security: bigger semgrep pack, ruff narrowed to bug+sec rules
scripts/static.sh /path/to/repo \
  --ruff-select 'F,E9,B,S' \
  --semgrep-config 'p/security-audit'

# JSON output to a file
scripts/static.sh /path/to/repo --out /tmp/static.json

# human-readable summary
scripts/static.sh /path/to/repo --format summary

# disable one runner (e.g. semgrep is slow on huge monorepos)
scripts/static.sh /path/to/repo --no-semgrep
```

From Python:

```python
from beigebox.skills.static import run_static

result = await run_static(
    "/path/to/repo",
    ruff_select="F,E9,B,S",
    semgrep_config="p/security-audit",
)
# result["findings"]    -> [garlicpress-shape Finding dicts, sorted by severity]
# result["stats"]       -> {ruff_count, semgrep_count, total_findings, durations, errors}
# result["raw_results"] -> {"ruff": {...}, "semgrep": {...}}  (raw runner output)
```

## CLI options

| Flag | Default | Meaning |
|------|---------|---------|
| `--ruff-select` | `F,E9,B,S,ASYNC,ARG,RUF,PL,TRY` | Ruff rule selection. Bug-and-security-biased; skips style noise by default. |
| `--ruff-ignore` | `None` | Ruff rules to ignore. Comma-separated. |
| `--semgrep-config` | `p/python` | Semgrep config: registry shortcut (`p/python`, `p/security-audit`, `p/owasp-top-ten`), file path, or comma-list. |
| `--no-ruff` | `false` | Skip the ruff runner. |
| `--no-semgrep` | `false` | Skip the semgrep runner. |
| `--ruff-timeout` | `120` | Seconds. |
| `--semgrep-timeout` | `600` | Seconds. Semgrep can be slow on the first run while it caches rules. |
| `--format` | `json` | `json` or `summary`. |
| `--out` | `None` | Write JSON output to this file. |

## Output shape

```json
{
  "findings": [
    {
      "finding_id": "static_<hash>",
      "severity": "high",
      "type": "security",
      "location": "src/api/routes.py:42",
      "description": "ruff/S301: Use of pickle, possible RCE on untrusted input",
      "evidence": "ruff rule S301\ndocs: https://...",
      "traceability": {"file": "src/api/routes.py", "line": 42, "git_sha": null},
      "static_meta": {"tool": "ruff", "rule_id": "S301", "column": 8, "url": "https://..."}
    }
  ],
  "stats": {
    "total_findings": 14,
    "ruff_count": 9,
    "semgrep_count": 5,
    "ruff_duration_seconds": 0.47,
    "semgrep_duration_seconds": 38.2,
    "ruff_error": null,
    "semgrep_error": null
  },
  "raw_results": {"ruff": {...}, "semgrep": {...}}
}
```

## Severity mapping

**Ruff** — by rule prefix, with high-severity bumps for the actually dangerous `S` rules:

| Prefix | Severity | Type | Notes |
|--------|----------|------|-------|
| `E9` | high | logic_error | Syntax errors |
| `F` | medium | logic_error | Pyflakes (undefined names, unused imports) |
| `B` | medium | logic_error | Bugbear |
| `S` (default) | medium | security | Bandit-port |
| `S102/301/307/311/324/501/502/506/602/605/608/701` | **high** | security | exec, pickle, eval, weak crypto/random/hash, SSL bypass, shell=True, SQL injection, jinja2 autoescape off |
| `ASYNC` | medium | logic_error | Async/await footguns |
| `ARG`, `RUF`, `PL`, `TRY` | low | logic_error | Style-adjacent bugs |
| `UP`, `SIM`, `I`, `E`, `W`, `N`, `D` | low | style | Pure style — disabled in default `--ruff-select` |

**Semgrep** — by `severity` field plus `metadata.category`:

| Field | Mapping |
|-------|---------|
| `severity: ERROR` | high |
| `severity: WARNING` | medium |
| `severity: INFO` | low |
| `metadata.category: security/vuln` | type=security |
| `metadata.category: correctness/best-practice` | type=logic_error |
| `metadata.category: performance` | type=resource_leak |

## Behavior notes

- **Per-runner failure isolation.** If ruff is missing or semgrep hits a network error fetching its rule pack, that runner's `error` field is set and the other runner's findings still come back. Pipeline never raises.
- **Parallel execution.** Ruff and semgrep run as concurrent subprocesses via `asyncio.create_subprocess_exec`; ruff usually finishes in <1s while semgrep is still pulling rules.
- **Dedupe.** Findings are deduped on `(location, tool, rule_id)`. Cross-tool overlap on the same line is kept — same bug found by two tools is signal, not noise.
- **Severity sort.** Findings come back sorted critical → high → medium → low, then by location.
- **Exit code.** CLI exits 1 if any high+ severity finding surfaced, 3 if both runners errored, 0 otherwise. (Ruff alone exits 0/1 by finding presence; we override that for the union case.)
- **Semgrep first run.** Pulls registry rules over the network and caches them in `~/.semgrep/`. Budget extra time on cold cache, ~zero overhead after.

## Requirements

- `python3` ≥ 3.11
- `ruff` on PATH (`pip install ruff` or already-bundled in many dev envs)
- `semgrep` on PATH (`pip install semgrep`)
- Either tool missing → that runner emits `error`; the skill still runs the other one

## Anti-patterns

- Don't use this for **type-checking** — `mypy` / `pyright` are a different category (they need full env resolution; static-skill is rule-based). If the user asks for type analysis, point them there.
- Don't crank `--semgrep-config p/r2c-ci` or similar **mega-pack** on a 100k-LOC monorepo without an `--out` file and a long `--semgrep-timeout` — semgrep can take 10+ min on big codebases with broad rule packs.
- Don't pair this with `bandit` — `ruff -S` already covers it. Picking both gives you duplicate findings with different IDs.
- Don't treat the output as ground truth without triage. SAST has a higher false-positive rate than fuzzing; expect to drop ~30% of findings as "intended pattern" or "context-aware safe" once a human reviews.
