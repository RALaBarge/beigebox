---
name: static
version: 3
description: Use when the user wants static analysis / SAST / type-checking / lint-with-teeth / dependency-CVE / secrets scanning on a Python codebase — finding security smells, logic bugs, type errors, vulnerable deps, and leaked credentials without running the code. Wraps ruff (full ruleset including bandit-port S rules), semgrep (registry-backed pattern + dataflow rules), mypy (type checking), pip-audit (SCA / dependency CVE scanning against OSV), and detect-secrets (credential / API-key leaks in source). Emits garlicpress-shape findings so the output merges with fuzz/static pipelines without translation. Five non-overlapping categories: ruff covers AST patterns + bandit; semgrep covers cross-file dataflow; mypy covers types; pip-audit covers known-CVE deps; detect-secrets covers leaked credentials. Skip bandit (ruff -S already covers it).
---

# static

`scripts/static.sh` (or `python3 -m beigebox.skills.static`) — run ruff + semgrep + mypy + pip-audit + detect-secrets against a Python repo and emit a single ranked finding list. Companion to the `fuzz` skill: same garlicpress-shape output, same async pipeline, callable from a Trinity orchestrator alongside fuzz.

## When to invoke

- User asks for "static analysis", "SAST", "find bugs without running", "lint with teeth", "dependency audit", "secret scan"
- An audit / review needs the static side of a static-plus-fuzz sweep
- Catching things `fuzz` can't reach: dead code, unused imports, deprecated APIs, type-confused branches that never run with random inputs, vulnerable pinned deps, and committed credentials

## Why these five tools

`ruff` is the bandit successor for Python — its `S` ruleset is a port of bandit (`exec`, `eval`, `pickle`, weak crypto, `subprocess(shell=True)`, etc.) and runs in milliseconds. Adding `bandit` separately would mostly re-find the same things.

`semgrep` covers ground ruff can't: cross-file dataflow, framework-specific patterns (Django, Flask, requests), and a curated registry of security/correctness rule packs. Slower, but reaches deeper.

`mypy` covers a category neither ruff nor semgrep can touch — type checking. Catches arg-type mismatches, missing attributes, `None` flowing into non-Optional parameters. Highest-bug-per-line of any static tool when applied to typed code.

`pip-audit` is SCA — it cross-references pinned dependencies in `requirements*.txt` against the OSV database and PyPI advisories. Most real-world Python CVEs ship through deps, not your code; without this category the static stack is blind to them.

`detect-secrets` is a regex + entropy scanner for committed credentials (AWS keys, GitHub tokens, private keys, JWTs, basic-auth strings). Cheap, fast, high-signal for the "someone hardcoded a key" class of finding.

So the five are **ruff (fast, AST-pattern, bandit-equivalent)** + **semgrep (slower, dataflow, registry)** + **mypy (types)** + **pip-audit (CVE deps)** + **detect-secrets (leaked secrets)**. Skip bandit; ruff covers it.

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

# skip dependency / secrets scans (e.g. running on a fixture dir)
scripts/static.sh /path/to/repo --no-pip-audit --no-secrets
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
| `--mypy-strict` | `false` | Run mypy in `--strict` mode. Much louder; lots of low-severity findings. |
| `--mypy-follow-imports` | `silent` | Mypy `--follow-imports` value. `silent` = don't recurse into untyped deps. |
| `--no-ruff` | `false` | Skip the ruff runner. |
| `--no-semgrep` | `false` | Skip the semgrep runner. |
| `--no-mypy` | `false` | Skip the mypy runner. |
| `--no-pip-audit` | `false` | Skip dependency CVE scanning. |
| `--no-secrets` | `false` | Skip the detect-secrets runner. |
| `--ruff-timeout` | `120` | Seconds. |
| `--semgrep-timeout` | `600` | Seconds. Semgrep can be slow on the first run while it caches rules. |
| `--mypy-timeout` | `300` | Seconds. Mypy can be slow on first run; `--no-incremental` disables caching for clean subprocess invocation. |
| `--pip-audit-timeout` | `180` | Seconds. Per requirements file. Network-bound (OSV lookup). |
| `--secrets-timeout` | `180` | Seconds. detect-secrets runs in-process Python regex + entropy scans. |
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

**Mypy** — by error level + error code:

| Field | Mapping |
|-------|---------|
| `level: error`, code in {`attr-defined`, `union-attr`, `call-arg`, `arg-type`, `return-value`, `assignment`, `operator`, `index`, `no-redef`, `valid-type`} | high / logic_error (would crash at runtime) |
| `level: error` (other codes) | medium / logic_error |
| `level: note` | low / logic_error (informational; "Revealed type is..." etc.) |

**pip-audit** — every CVE / advisory hit:

| Field | Mapping |
|-------|---------|
| any vuln in `dependencies[].vulns[]` | high / security |

CVSS data isn't reliably present in pip-audit's JSON output, so we don't try to refine severity. A pinned dep with a known advisory is high, full stop. Triage is for downgrading false positives.

**detect-secrets** — by detector type and verification status:

| Field | Mapping |
|-------|---------|
| `is_verified: true` (any detector) | critical / security |
| Detector in {`Secret Keyword`, `Base64 High Entropy String`, `Hex High Entropy String`} | medium / security (high FP rate — fixtures, hashes-not-secrets) |
| Any other detector (`AWS Access Key`, `GitHub Token`, `Private Key`, `Stripe`, `Slack`, etc.) | high / security |

## Behavior notes

- **Per-runner failure isolation.** If ruff is missing, semgrep hits a network error fetching its rule pack, or pip-audit can't reach OSV, that runner's `error` field is set and the other runners' findings still come back. Pipeline never raises.
- **Parallel execution.** All five runners launch as concurrent subprocesses via `asyncio.create_subprocess_exec`; ruff and detect-secrets usually finish in <1s while semgrep is still pulling rules and pip-audit is talking to OSV.
- **Dedupe.** Findings are deduped on `(location, tool, rule_id)`. Cross-tool overlap on the same line is kept — same bug found by two tools is signal, not noise.
- **Severity sort.** Findings come back sorted critical → high → medium → low, then by location.
- **Exit code.** CLI exits 1 if any high+ severity finding surfaced, 3 if every enabled runner errored, 0 otherwise.
- **Semgrep first run.** Pulls registry rules over the network and caches them in `~/.semgrep/`. Budget extra time on cold cache, ~zero overhead after.
- **pip-audit manifest discovery.** Auto-finds `requirements.txt`, `requirements-*.txt`, `requirements_*.txt`, and `requirements/*.txt` at the repo root and one level deep. Skips `.venv`, `node_modules`, `.tox`, `__pycache__`, `dist`, `build`, etc. No manifests found = silent skip (not an error).
- **pip-audit `--no-deps`.** We pass `--no-deps` so pip-audit only audits exactly what's pinned in the requirements file, without triggering a transitive resolution. Faster, deterministic, no surprises in CI. Caller who wants deep transitive auditing can run pip-audit standalone.
- **detect-secrets line numbers.** Reported as 1-based line in the file. The literal secret value is *not* stored — we keep the SHA-1 from detect-secrets in `static_meta.extra.hashed_secret` for de-dup, not for replay.

## Requirements

- `python3` ≥ 3.11
- `ruff` on PATH (`pip install ruff` or already-bundled in many dev envs)
- `semgrep` on PATH (`pip install semgrep`)
- `mypy` on PATH (`pip install mypy`)
- `pip-audit` on PATH (`pipx install pip-audit`)
- `detect-secrets` on PATH (`pipx install detect-secrets`)
- Any tool missing → that runner emits `error`; the skill still runs the others

## Anti-patterns

- Don't crank `--semgrep-config p/r2c-ci` or similar **mega-pack** on a 100k-LOC monorepo without an `--out` file and a long `--semgrep-timeout` — semgrep can take 10+ min on big codebases with broad rule packs.
- Don't pair this with `bandit` — `ruff -S` already covers it. Picking both gives you duplicate findings with different IDs.
- Don't pair this with a separate `safety` / `pip-audit` invocation — this skill already runs pip-audit. Picking both gives you duplicate dependency findings.
- Don't pair this with a separate `gitleaks` / `trufflehog` pass on the same tree without a reason — detect-secrets covers the working-tree case. Use gitleaks if you specifically need to scan **git history**, which detect-secrets doesn't do.
- Don't treat the output as ground truth without triage. SAST has a higher false-positive rate than fuzzing; expect to drop ~30% of findings as "intended pattern" or "context-aware safe" once a human reviews. The `Secret Keyword` and entropy detectors in detect-secrets are particularly noisy on test fixtures and pre-hashed values.
