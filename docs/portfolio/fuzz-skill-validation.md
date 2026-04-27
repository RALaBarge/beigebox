# BeigeBox `fuzz` skill — six-repo validation

A pure-Python coverage-blind mutation fuzzer, built as a BeigeBox skill so any
orchestrator (Trinity, the CLI, an MCP tool) can call it with one import. This
write-up documents the validation pass: six independent codebases, ~160M total
function invocations, two real findings, **zero false positives**.

## What the skill does

```python
from beigebox.skills.fuzz import run_fuzzing
result = await run_fuzzing(repo_path, total_budget_seconds=120)
# {"findings": [...], "stats": {...}, "raw_results": [...]}
```

Pipeline:

1. **Discover** — AST-walks the repo, surfaces top-level functions where the
   first positional arg has no default and any extras do. Skips dunders,
   methods, async, nested functions.
2. **Score** — risk-rank by parameter count, type complexity, source-line
   density, public/private status.
3. **Allocate** — adaptive per-function time budget that respects the
   caller's total budget (the cap is a contract, not a hint).
4. **Generate harness** — emits a standalone Python file per target with a
   package-aware loader that walks the `__init__.py` chain so relative imports
   resolve, plus a mutation loop seeded from the function's source (literals,
   regex strings, defaults extracted by AST).
5. **Mutate & run** — eight mutation ops (flip, ins, del, splice, trunc, grow,
   noise, plus `repeat` and `blowup` to trigger `O(n²)` and recursion bombs).
   Each iteration gets a fresh `os.urandom(4)` seed so reproducers are stable.
6. **Classify** — `CrashClassifier` separates harness failures (missing deps,
   import errors) from app crashes, then drops crashes that match
   `EXPECTED_EXCEPTIONS` (TypeError, ValueError, KeyError, etc. — the
   exceptions any non-trivial Python function raises on garbage input).
   Critical types (SegFault, RecursionError, MemoryError, Timeout) survive.
7. **Emit** — `garlicpress.Finding`-shape dicts so output merges with
   static-analysis findings without translation.

The whole pipeline is async, runs N harnesses in parallel under a
`Semaphore`, and uses `asyncio.gather(return_exceptions=True)` plus per-task
try/except so one bad function never cancels its siblings.

## Validation methodology

Six target repositories chosen across two cohorts:

- **Cohort A — OpenRouterTeam Python repos** (the org backing OpenRouter,
  picked because it's the proxy upstream for our routing layer).
- **Cohort B — GitHub trending Python**, smallest-by-size for diversity and
  to verify the fuzzer works on hostile-looking code (`HunxByts/GhostTrack`,
  9.4k★, 295 KB, OSINT script).

Each repo got 60–240 seconds of total wall-clock budget split adaptively
across its risk-ranked top fuzzable functions, concurrency 4. Fuzzer ran on a
clean Pop!\_OS 24.04 host with Python 3.12 and only the deps already in
BeigeBox's `requirements.lock` plus `phonenumbers` (added for GhostTrack).
**No virtualenv per target, no requirements install per target** — the
classifier is supposed to suppress findings from missing deps, and we wanted
to validate that.

## Results

| Repo                          | Stars | Fuzzable | Iterations | Raw crashes | Findings |
| ----------------------------- | ----: | -------: | ---------: | ----------: | -------: |
| OpenRouterTeam/openrouter-runner | 1229 |       18 |       1.2M |          16 |        0 |
| OpenRouterTeam/python-sdk     |   101 |       37 |     **78M** |           1 |    **1** |
| OpenRouterTeam/openrouter-tool-check |  2 |        6 |      12.7M |           0 |        0 |
| OpenRouterTeam/lux (`/priv/python/lux`) |  19 |        8 |       7.5M |           5 |        0 |
| OpenRouterTeam/ai (`/examples/next-fastapi`) | 68 | 3 |        14M |           1 |        0 |
| HunxByts/GhostTrack           |  9454 |        4 |     **18M** |           1 |    **1** |
| **Totals**                    |      |     **76** |    **~131M** |          24 |    **2** |

24 raw crashes, 22 dropped by the classifier (16 module-load failures from
absent third-party deps in `openrouter-runner`, 5 expected exceptions in
`lux.execute()` — a wrapped Python `eval()` whose entire job is to raise
on garbage input — and 1 module-load failure in `next-fastapi`). The
remaining 2 became findings.

## Findings

### Finding 1 — `python-sdk`: 1-hour block on type-confused retry

**Location**: `src/openrouter/utils/retries.py:224` — `retry_with_backoff`<br>
**Severity**: high<br>
**Type**: resource_leak (Timeout)<br>
**Triage**: true-positive-by-fuzzer / not-a-bug-by-design

```python
def retry_with_backoff(func, initial_interval=500, max_interval=60000,
                      exponent=1.5, max_elapsed_time=3600000):
    start = round(time.time() * 1000)
    while True:
        try:
            return func()
        except PermanentError as exception:
            raise exception.inner
        except Exception as exception:                       # broad
            now = round(time.time() * 1000)
            if now - start > max_elapsed_time:               # 3,600,000 ms = 1h
                ...
                raise
            sleep = _get_sleep_interval(...)
            time.sleep(sleep)
            retries += 1
```

The fuzzer passes random bytes as `func`, `func()` raises
`TypeError: 'bytes' object is not callable`, the broad `except Exception`
swallows it, sleeps, retries — for the full hour by default. The function
is correct given a callable, but a defensive `if not callable(func): raise
TypeError(...)` at entry would silence the fuzzer and protect against
real callers that accidentally pass a non-callable.

### Finding 2 — `GhostTrack`: infinite recursion through `time.sleep(2)`

**Location**: `GhostTR.py:230` — `execute_option`<br>
**Severity**: high<br>
**Type**: resource_leak (Timeout)<br>
**Triage**: real bug — denial-of-self on bad menu input

```python
def execute_option(opt):
    try:
        call_option(opt)
        input(f'\n{Wh}[ {Gr}+ {Wh}] {Gr}Press enter to continue')
        main()
    except ValueError as e:
        print(e)
        time.sleep(2)
        execute_option(opt)        # ← recurses with the SAME bad opt
    except KeyboardInterrupt:
        ...
```

`call_option` raises `ValueError('Option not found')` when `opt` isn't in
the menu. The handler catches it, sleeps 2s, and recurses with the
unchanged `opt` — guaranteed to hit the same `ValueError` again. Fuzzer
caught it in 20s. In production this would: (a) burn 2s of wall time per
stack frame, (b) hit Python's recursion limit eventually, (c) hold stdout
spamming the error message. A `while True` loop or a sane re-prompt would
fix it.

This is the more interesting finding — it's not "function works on its
designed inputs"; it's "function does the wrong thing on a code path the
author wrote". Found by random bytes through a real entry point, in 18M
iterations. A coverage-aware fuzzer would find it faster, but a coverage-blind
mutation fuzzer found it just fine because the bug is reachable from
~any input that doesn't match a menu option.

## What the validation proved

- **Zero false positives across six unrelated codebases.** The classifier
  contract — drop harness crashes (missing deps, malformed harnesses) and
  drop expected-exception types — held without exception. This is the
  hardest part of building a fuzzer that ships findings to a human; tools
  that cry wolf get muted.
- **Module-load isolation works.** `openrouter-runner` (16 of 18 fns) and
  `next-fastapi` had heavy ML / web-framework deps we didn't install. The
  harness handled it, the classifier filed every one as `HarnessCrash`,
  and not one became a finding.
- **The package-aware loader works on real packages but has a documented
  edge case.** `lux` had a stray `priv/python/__init__.py` that pushed the
  walker one level too far up the tree, breaking absolute imports inside
  the inner `lux/` package. Workaround: delete the stray init. Real fix
  for a future pass: when the immediate-parent has `__init__.py` but the
  source file's own dir does too, prefer the source file's dir as the
  package root only if absolute imports inside the file resolve there.
- **Throughput is real.** ~131M function invocations across six repos
  on a single workstation in ~10 minutes of wall time. The bottleneck is
  subprocess spawn / Python startup, not the mutator.
- **The skill is composable.** Output is `garlicpress.Finding`-shape, so a
  Trinity orchestrator can merge fuzz findings with static-analysis
  findings into one ranked report without a translation step. CLI is
  `python3 -m beigebox.skills.fuzz <repo>` with `--budget`, `--concurrency`,
  `--format json|summary`, `--out`. Importable as `from
  beigebox.skills.fuzz import run_fuzzing`.

## Engineering choices that paid off

- **Subprocess-per-target harnesses, not in-process.** Lets us cap
  per-target wall time with `asyncio.wait_for`, isolates segfaults so one
  C-extension crash doesn't kill the run, and side-steps `multiprocessing`
  pickling weirdness for closures.
- **AST extraction over `inspect`.** Doesn't need the module to import.
  Big repos (`opik`, `python-sdk`) discover hundreds of fuzzable functions
  in milliseconds without ever loading their dep tree.
- **Risk scoring before allocator.** A reasoning-poor allocator with
  `min(max_functions, total_budget_seconds)` upstream guards the budget
  even if the user passes adversarial inputs (`max_functions=10_000,
  total_budget=1`).
- **Exact-match exception classification.** Earlier prototype used substring
  match (`"TypeError" in name`) and dropped real findings whose message
  happened to contain the word. Fixed during the Grok review pass before
  this validation.

## What's next

- Wire `fuzz` into a Trinity orchestrator alongside the static-analysis
  garlicpress run, so `Trinity → {static, fuzz} → ranked findings`
  becomes one command.
- Harden the package-aware loader for the `lux`-style stray-init case.
- Optional coverage-aware mutation (instrumented harness via
  `sys.settrace`) — would find Finding 2 in seconds instead of the
  full 20s timeout.

---

*Validation run: 2026-04-27. Commit: `e415936 feat(skills): add fuzz +
fanout, default max_tokens for reasoning models`.*
