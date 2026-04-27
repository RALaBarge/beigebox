---
name: fuzz
version: 1
description: Use when the user wants to fuzz / find DOS or crash bugs in / dynamic-analyze a Python repository, or wants the dynamic complement to a static-analysis pass (garlicpress, Trinity). Walks the repo, picks the highest-risk fuzzable functions, runs each in an isolated subprocess harness with a mutation loop seeded from docstrings + parser edge cases, and emits garlicpress-shape Finding dicts. Python-only; output is JSON or a one-line-per-finding summary.
---

# fuzz

`scripts/fuzz.sh` (or `python3 -m beigebox.skills.fuzz`) — discover Python functions in a repo, score them by vulnerability likelihood, fuzz the top N in parallel subprocesses, and emit findings in the same shape as `garlicpress.Finding` so a static + dynamic run can be merged downstream. Designed to be called as Phase 1b of a Trinity-style audit (parallel with the static analyzers) but works standalone.

## When to invoke

- User asks to "fuzz this repo" / "find DOS bugs" / "find recursion or memory bugs" / "do dynamic analysis on …"
- An audit has produced static findings and the user wants the dynamic complement before drawing conclusions
- A Trinity / multi-model audit pipeline needs to merge fuzzing results with static-analysis findings

## Usage

```bash
# fuzz a Python repo with defaults (top 25 functions, 120s total budget)
scripts/fuzz.sh /path/to/repo

# tighter scope and stricter budget for CI
scripts/fuzz.sh --max-functions 10 --budget 30 /path/to/repo

# human-readable summary (one line per finding) instead of JSON
scripts/fuzz.sh --format summary /path/to/repo

# write JSON to a file
scripts/fuzz.sh --out findings.json /path/to/repo

# raise per-harness parallelism on a beefier box
scripts/fuzz.sh --concurrency 4 /path/to/repo

# direct module invocation (skips the bash wrapper; same flags)
python3 -m beigebox.skills.fuzz /path/to/repo --budget 60
```

From a Trinity (or any other) orchestrator, prefer the importable entrypoint:

```python
from beigebox.skills.fuzz import run_fuzzing

result = await run_fuzzing(repo_path, max_functions=25, total_budget_seconds=120)
# result["findings"]  -> list of garlicpress-shape Finding dicts
# result["stats"]     -> {functions_discovered, functions_fuzzed, total_iterations, ...}
# result["raw_results"] -> per-function raw fuzz output
```

## Output shape

Each entry in `findings` is shaped to drop into a `garlicpress.Finding`:

```json
{
  "finding_id": "fuzz_a1b2c3d4e5f6",
  "severity": "high",
  "type": "resource_leak",
  "location": "path/to/file.py:42",
  "description": "Fuzzer found RecursionError in parse_json: maximum recursion depth exceeded",
  "evidence": "<truncated stack trace>",
  "traceability": {"file": "path/to/file.py", "line": 42, "git_sha": null},
  "fuzz_meta": {"function": "parse_json", "crash_type": "RecursionError",
                "reproducer_hex": "...", "iterations": 1234, "duration_seconds": 5.2}
}
```

Crash type → finding mapping:

| Crash type        | Severity | Finding type   |
|-------------------|----------|----------------|
| RecursionError    | high     | resource_leak  |
| MemoryError       | high     | resource_leak  |
| Timeout           | high     | resource_leak  |
| AssertionError    | medium   | logic_error    |
| SegmentationFault | critical | security       |
| HarnessCrash      | low      | other          |

## How it works

1. **Extract** — `FunctionExtractor` AST-walks every `*.py` under the repo (skipping `.venv`, `__pycache__`, `.git`, `node_modules`, `.tox`, `.mypy_cache`). Functions with no parameters, `self`-only, dunder methods, or single-line bodies are dropped.
2. **Score** — `RiskScorer` assigns 1–10. Parsing/decoding names get +4, processing +3, crypto +2; loops/recursion +1–2; private/trivial/asserted/no-return functions get penalties.
3. **Allocate** — `AdaptiveTimeAllocator` distributes the total budget across the top `max_functions`, weighted by risk and complexity.
4. **Seed + harness** — `SeedCorpusExtractor` mines docstring/comment string literals, plus parser edge cases (empty, null byte, deep nesting, invalid UTF-8) for parser-named functions. `SmartHarnessGenerator` infers parameter type from hints and usage, then emits a standalone harness that imports the target by file path, loads the seed corpus, and runs a mutation loop (bit/byte flip, insert, delete, splice, truncate, grow, noise) until its per-function budget expires.
5. **Run** — `Fuzzer` runs each harness in its own subprocess with a hard outer timeout. Crashes are read from the harness's JSON output line; a non-zero exit with no JSON crash is recorded as a `HarnessCrash`.
6. **Classify** — `CrashClassifier` filters out library frames (`/site-packages/`, `/lib/python`, `<frozen`), expected validation exceptions (`ValueError`, `KeyError`, `TypeError`, `AttributeError`, `IndexError`), and any crash whose stack does not include the repo root. Surviving crashes become findings.

## Requirements

- `python3` ≥ 3.11 with `beigebox` importable (this repo's `.venv` qualifies — `pip install -e .` from repo root)
- No third-party fuzzing library required. The harness is pure-Python: AST walk, mutation loop, subprocess isolation
- Read access to the target repo

## Behavior notes

- **Python only.** Other languages are ignored. Extending to other languages is a separate skill, not a feature flag.
- **Subprocess isolation.** A crashing target cannot crash the parent. Each harness gets a wall-clock timeout (per-function allocation + 5s grace).
- **Reproducer hex.** When a crash is reported, the input is recorded in `fuzz_meta.reproducer_hex` so the user can replay (`bytes.fromhex(...)`) outside the fuzzer.
- **Side-effect risk.** The harness imports the target's module, which executes module-level code in the subprocess. Don't point at modules with destructive imports (formatting disks, sending mail, etc.); same caveat as any code-coverage tool.
- **Coverage-blind.** This is a pure mutation fuzzer with no coverage feedback — fine for DOS / unbounded recursion / memory-blowup classes, weak for deep logic bugs that need symbolic execution. Plan accordingly.
- **Per-function dedup.** Crashes are deduplicated within a function by `(type, last-frame-of-traceback)`; you'll get one finding per distinct shape, not one per mutation that triggered it.

## Anti-patterns

- Do not invoke this skill on the standard library or any pip-installed package — the classifier filters out library-path crashes by design.
- Do not use the harness's iteration count for performance benchmarks. It's a real iteration count, but it's wall-clock-bounded and varies wildly with target speed.
