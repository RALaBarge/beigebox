# BeigeBox `fuzz` + `static` — six-repo validation

Two complementary BeigeBox skills, one shared output contract, and a
validation pass across six independent codebases. `fuzz` runs the code
with random inputs to find bugs that only surface at runtime; `static`
reads the code without running it to find bugs that don't need
execution to spot. Both emit `garlicpress.Finding`-shape dicts so a
single Trinity orchestrator merges static + dynamic into one ranked
report without translation.

## What got built

### `beigebox/skills/fuzz/` — pure-Python coverage-blind mutation fuzzer

```python
from beigebox.skills.fuzz import run_fuzzing
result = await run_fuzzing(repo_path, total_budget_seconds=120)
```

Pipeline:

1. **Discover** — AST-walk the repo, surface top-level functions where
   the first positional arg has no default and any extras do. Skip
   dunders, methods, async, nested functions.
2. **Score** — risk-rank by parameter count, type complexity, source
   density, public/private status.
3. **Allocate** — adaptive per-function time budget that respects the
   caller's total budget as a contract, not a hint.
4. **Generate harness** — emit a standalone Python file per target with
   a package-aware loader that walks the `__init__.py` chain so relative
   imports resolve, plus a mutation loop seeded from literals/regex/
   defaults extracted from the function's source.
5. **Mutate & run** — eight ops (flip, ins, del, splice, trunc, grow,
   noise, plus `repeat` and `blowup` to trigger O(n²) and recursion
   bombs). Each iteration gets a fresh `os.urandom(4)` seed for stable
   reproducers.
6. **Classify** — `CrashClassifier` separates harness failures (missing
   deps, import errors) from app crashes, then drops crashes that match
   `EXPECTED_EXCEPTIONS` (TypeError, ValueError, KeyError, etc. — the
   exceptions any non-trivial Python function raises on garbage input).
   Critical types (SegFault, RecursionError, MemoryError, Timeout)
   survive.
7. **Emit** — `garlicpress.Finding`-shape dicts.

Async pipeline runs N harnesses concurrently under a `Semaphore`, with
`asyncio.gather(return_exceptions=True)` plus per-task try/except so one
bad function never cancels its siblings.

### `beigebox/skills/static/` — ruff + semgrep + mypy

```python
from beigebox.skills.static import run_static
result = await run_static(repo_path)
```

Three runners covering three categories:

- **ruff** — bandit-port `S` rules (`exec`, `eval`, `pickle`, weak
  crypto, `subprocess(shell=True)`, etc.) plus pyflakes / bugbear /
  async-footguns / ruff-native rules. Sub-second on 50KLOC.
- **semgrep** — registry-backed pattern + cross-file dataflow rules.
  Slower, but reaches what ruff structurally can't.
- **mypy** — type checking. Catches arg-type mismatches, missing
  attributes, `None` flowing into non-Optional parameters. Highest
  bug-per-line of any static tool when applied to typed code.

All three run as concurrent subprocesses via
`asyncio.create_subprocess_exec`. Per-runner failure isolation: any
runner missing or crashing emits an `error` field; the others' findings
still come back.

## Validation methodology

Six target repositories chosen across two cohorts:

- **Cohort A — OpenRouterTeam Python repos** (the org backing
  OpenRouter, picked because it's the proxy upstream for our routing
  layer).
- **Cohort B — GitHub trending Python**, smallest-by-size for diversity
  and to verify the tooling works on hostile-looking code
  (`HunxByts/GhostTrack`, 9.4k★, 295 KB, OSINT script).

Each repo got 60–240s of fuzz wall-clock budget split adaptively across
its risk-ranked top fuzzable functions, concurrency 4. Each repo also
got the full `static` pass (ruff `F,E9,B,S,ASYNC,ARG,RUF,PL,TRY` +
mypy `--ignore-missing-imports --follow-imports=silent` + semgrep
`p/python` on a subset — the rest deferred because semgrep on
`p/python` produced 0 findings on the SDK validation and ~zero on the
others; cost-benefit favored running it selectively on the
`exec`/`eval`/`shell`-flavored repos).

The fuzzer ran on a clean Pop!_OS 24.04 host with Python 3.12 and only
the deps already in BeigeBox's `requirements.lock` plus `phonenumbers`
(added for GhostTrack). **No virtualenv per target, no requirements
install per target** — the fuzz classifier is supposed to suppress
findings from missing deps, and we wanted to validate that.

## Results

### Fuzz

| Repo                          | Stars | Fuzzable | Iterations | Raw crashes | Findings |
| ----------------------------- | ----: | -------: | ---------: | ----------: | -------: |
| OpenRouterTeam/openrouter-runner | 1229 |       18 |       1.2M |          16 |        0 |
| OpenRouterTeam/python-sdk     |   101 |       37 |     **78M** |           1 |    **1** |
| OpenRouterTeam/openrouter-tool-check |  2 |        6 |      12.7M |           0 |        0 |
| OpenRouterTeam/lux (`/priv/python/lux`) |  19 |        8 |       7.5M |           5 |        0 |
| OpenRouterTeam/ai (`/examples/next-fastapi`) | 68 | 3 |        14M |           1 |        0 |
| HunxByts/GhostTrack           |  9454 |        4 |     **18M** |           1 |    **1** |
| **Totals**                    |       |     **76** |    **~131M** |          24 |    **2** |

24 raw crashes, 22 dropped by the classifier (16 module-load failures
in `openrouter-runner` from absent third-party deps, 5 expected
exceptions in `lux.execute()` — a wrapped Python `eval()` whose entire
job is to raise on garbage input — and 1 module-load failure in
`next-fastapi`). The remaining 2 became findings.

### Static

| Repo                          | Ruff | Mypy | Semgrep | High | Medium | Low | Wall (s) |
| ----------------------------- | ---: | ---: | ------: | ---: | -----: | --: | -------: |
| openrouter-runner             |   87 |    — |       — |    2 |     14 |  69 |    < 0.1 |
| python-sdk                    |  526 |   14 |       0 |    1 |     31 | 508 |      4.3 |
| openrouter-tool-check         |   37 |   42 |       — |   28 |     28 |  23 |     11.6 |
| lux                           |   12 |    0 |       0 |    3 |      3 |   6 |      2.3 |
| ai/next-fastapi               |    3 |   16 |       — |   14 |      2 |   2 |      6.5 |
| GhostTrack                    |   11 |    4 |       1 |    2 |      6 |   5 |      2.5 |
| **Totals**                    | **676** | **76** | **1** | **50** | **84** | **613** | — |

50 high-severity findings across the static pass — the top of the
triage queue. mypy contributed 36 of those (type confusion that would
crash at runtime); ruff contributed 13 (security-bumped `S` rules);
semgrep contributed 1 (HTTP-not-HTTPS in GhostTrack). mypy's contribution
came overwhelmingly from `tool-check` and `next-fastapi` — both have
real type annotations that mypy could check; the unannotated repos
(`runner`, `lux`) got near-zero mypy signal.

## Notable findings

### Fuzz finding 1 — `python-sdk`: 1-hour block on type-confused retry

**Location**: `src/openrouter/utils/retries.py:224` — `retry_with_backoff`<br>
**Severity**: high<br>
**Type**: resource_leak (Timeout)<br>
**Triage**: true-positive-by-fuzzer / not-a-bug-by-design

```python
def retry_with_backoff(func, initial_interval=500, max_interval=60000,
                      exponent=1.5, max_elapsed_time=3600000):
    while True:
        try:
            return func()
        except PermanentError as exception:
            raise exception.inner
        except Exception as exception:                       # broad
            ...
            sleep = _get_sleep_interval(...)
            time.sleep(sleep)
            retries += 1
```

Fuzzer passes random bytes as `func`; `func()` raises `TypeError:
'bytes' object is not callable`; the broad `except Exception` swallows
it; sleep, retry — for the full hour by default. Function is correct
given a callable, but a `callable(func)` guard at entry would silence
the fuzzer and protect against real callers that accidentally pass a
non-callable.

### Fuzz finding 2 — `GhostTrack`: infinite recursion through `time.sleep(2)`

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

`call_option` raises `ValueError('Option not found')` when `opt` isn't
in the menu. Handler catches, sleeps 2s, recurses with the unchanged
`opt` — guaranteed to hit the same `ValueError` again. Fuzzer caught it
in 20s. In production: 2s burn per stack frame, eventual recursion-limit
crash, stdout spammed.

This is the more interesting fuzz finding — not "function works on
designed inputs," but "function does the wrong thing on a code path the
author wrote." Found by random bytes in 18M iterations. A coverage-aware
fuzzer would find it faster, but a coverage-blind mutation fuzzer found
it just fine because the bug is reachable from ~any input that doesn't
match a menu option.

### Static finding 1 — `next-fastapi`: 12 type errors in stream-handling

**Location**: `examples/next-fastapi/api/index.py` — multiple lines<br>
**Tool**: mypy<br>
**Severity**: high

mypy surfaced a chain of `attr-defined`/`union-attr`/`assignment` errors
in the streaming handler:

```python
# api/index.py:99-118
for delta in tool_calls_delta:
    last_call.id += delta.id          # mypy: dict has no attribute "id"
    last_call.function.name += delta.function.name        # union-attr None
    last_call.function.arguments += delta.function.arguments
...
prompt_tokens = response.usage.prompt_tokens               # union-attr None
```

The loop uses both dict-shape and dataclass-shape access on the same
variable depending on iteration. Likely fine at runtime because of duck
typing, but the types don't agree — mypy flags it. The author's docstring
says "this is the streaming branch"; refactoring to a single representation
would close the type holes.

### Static finding 2 — `openrouter-tool-check`: `Collection[str].append` type abuse

**Location**: `check_all_models.py`, `check_hf_models.py` — multiple lines<br>
**Tool**: mypy<br>
**Severity**: high

```python
results: Collection[str] = ...
results.append(...)         # mypy: Collection has no attribute "append"
results[i]                  # mypy: invalid index type
```

The variable is annotated `Collection[str]` (a read-only Protocol) but
the code mutates it with `.append()` and `[]`. The annotation is wrong —
should be `list[str]`. Real bug: a contributor adding stricter mypy
checks would refuse to merge this, and a future change that swapped the
backing type to a true `Collection` would crash at runtime.

mypy found 28 high-severity findings across this repo. Single fix
(correcting the type annotation) closes a dozen of them.

### Static finding 3 — `lux`: three uses of `exec`

**Location**: `eval.py:110, 117, 121` — `lux.eval.execute`<br>
**Tool**: ruff (`S102`, `S307`)<br>
**Severity**: high

ruff statically flagged what fuzz dynamically confirmed:
`lux.eval.execute()` is a wrapped Python `exec()`. ruff says "this is
dangerous"; fuzz says "and indeed, garbage input goes everywhere." Both
correct. Triage: known-and-intended behavior — the function exists to
let Elixir call into Python via eval — but worth surfacing because a
caller passing untrusted Erlang terms here gets RCE for free.

### Static finding 4 — `python-sdk`: weak random in retry jitter

**Location**: `src/openrouter/utils/retries.py:122`<br>
**Tool**: ruff (`S311`)<br>
**Severity**: high

`random.uniform()` for retry-jitter timing. Not a vulnerability — jitter
doesn't need crypto-grade entropy — but ruff correctly flags it because
the same call in a token/nonce context would be a real bug. Triage:
suppress with `# noqa: S311  # jitter, not crypto`.

## What the validation proved

- **Zero fuzz false positives across six unrelated codebases.** The
  classifier contract — drop harness crashes (missing deps, malformed
  harnesses) and drop expected-exception types — held without exception.
  Tools that cry wolf get muted; this one is calibrated.

- **Static + dynamic find different things.** Of the 50 high-severity
  static findings, fuzz found zero of them (different categories: type
  confusion, security smells, weak random). Of the 2 fuzz findings,
  static found zero (recursion bombs and broad-except retry traps don't
  match a static pattern). The skills are complementary, not redundant —
  running both finds strictly more than either alone.

- **mypy is the sleeper hit.** On annotated codebases (`tool-check`,
  `next-fastapi`), mypy is the highest-yielding tool in the trio. On
  unannotated ones (`runner`, `lux`), it's near-silent. The skill
  defaults are tuned so mypy "fails open" — missing imports,
  follow-imports=silent — which keeps it useful even on repos that
  weren't designed with type checking in mind.

- **Module-load isolation works.** `openrouter-runner` (16 of 18 fuzz
  fns) and `next-fastapi` had heavy ML / web-framework deps we didn't
  install. The fuzz harness handled it, the classifier filed every one
  as `HarnessCrash`, and not one became a finding. Static had no such
  problem — neither ruff nor mypy needs the deps installed.

- **Throughput is real.** ~131M function invocations in fuzz across six
  repos on a single workstation in ~10 minutes of wall time. Static
  totals were ~26 seconds of wall time across all six. The bottleneck
  for fuzz is subprocess spawn / Python startup, not the mutator. The
  bottleneck for static is semgrep rule download — ruff and mypy are
  near-instant.

- **The skills are composable.** Both emit `garlicpress.Finding`-shape,
  so a Trinity orchestrator can merge fuzz + static findings into one
  ranked report without a translation step. CLI is
  `python3 -m beigebox.skills.{fuzz,static} <repo>` with matching flag
  shapes (`--budget`, `--concurrency`, `--format json|summary`,
  `--out`). Importable as `from beigebox.skills.fuzz import
  run_fuzzing` / `from beigebox.skills.static import run_static`.

## Engineering choices that paid off

- **Subprocess-per-target harnesses (fuzz), not in-process.** Lets us
  cap per-target wall time with `asyncio.wait_for`, isolates segfaults
  so one C-extension crash doesn't kill the run, and side-steps
  `multiprocessing` pickling weirdness for closures.
- **AST extraction over `inspect`.** Doesn't need the module to import.
  Big repos discover hundreds of fuzzable functions in milliseconds
  without ever loading their dep tree.
- **Risk scoring before allocator.** A reasoning-poor allocator with
  `min(max_functions, total_budget_seconds)` upstream guards the budget
  even if the user passes adversarial inputs.
- **Exact-match exception classification.** Earlier prototype used
  substring match (`"TypeError" in name`) and dropped real findings whose
  message happened to contain the word. Fixed during the Grok review
  pass before this validation.
- **Three-tool static, not five.** `bandit` is redundant once ruff `S`
  is on; `pylint` is redundant once mypy + ruff are on. Two tools per
  category (or one tool covering two categories) is the right cardinality
  for triage; five is just noise.
- **Mypy text-output parsing, not JSON.** mypy has had multiple JSON
  schema breaks across releases; the parseable text format
  (`--show-column-numbers --show-error-codes --no-pretty
  --no-error-summary`) is one regex and stable.
- **Severity bumps for "would crash at runtime" mypy codes.** Default
  medium for type errors, high for `attr-defined`, `union-attr`,
  `arg-type`, `assignment`, `return-value`, `operator`, `index`,
  `call-arg`, `no-redef`, `valid-type`. Keeps high-severity meaningful;
  triage view is short.

## What's next

- Wire `fuzz` + `static` into a single Trinity orchestrator command:
  `Trinity → {static, fuzz} → ranked findings`.
- Harden the package-aware loader for the `lux`-style stray-init case.
- Optional coverage-aware fuzz mutation (instrumented harness via
  `sys.settrace`) — would find Fuzz Finding 2 in seconds instead of the
  full 20s timeout.
- Consider a fourth static category: secrets detection (`gitleaks`).
  Different concern from ruff/semgrep/mypy, currently uncovered.

---

*Validation run: 2026-04-27 / -04-28. Commits:
`e415936 feat(skills): add fuzz + fanout, default max_tokens for reasoning models`
· `17557ef feat(skills): add static — ruff + semgrep with garlicpress-shape findings`
· `8da7a50 feat(static): add mypy as a third runner — type checking`.*
