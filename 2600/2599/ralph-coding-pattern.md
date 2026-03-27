# ✅ COMPLETE — Implemented and archived (pre-2026-03-16)

# Ralph Coding Pattern — Reference & BeigeBox Implementation

## Origin

The **Ralph Wiggum Loop** is an autonomous LLM coding pattern that emerged from
the Claude Code community in 2025 and became a dominant paradigm by 2026.
Named after the lovably persistent Simpsons character, it embodies the philosophy
that **naive persistence beats sophisticated complexity** for agentic coding tasks.

The original minimal form, credited to Geoffrey Huntley:

```bash
while :; do cat PROMPT.md | claude --continue; done
```

The agent runs, produces output, and then runs again with a fresh context window —
picking up where it left off through the filesystem and git history rather than
conversation context.

Key references:
- [everything is a ralph loop — ghuntley.com](https://ghuntley.com/loop/)
- [github.com/ghuntley/how-to-ralph-wiggum](https://github.com/ghuntley/how-to-ralph-wiggum)
- [Ralph Wiggum Loop vs Open Spec — redreamality.com](https://redreamality.com/blog/ralph-wiggum-loop-vs-open-spec/)
- [From ReAct to Ralph Loop — Alibaba Cloud](https://www.alibabacloud.com/blog/from-react-to-ralph-loop-a-continuous-iteration-paradigm-for-ai-agents_602799)

---

## The Core Idea

Traditional agentic coding fails because of **context rot**: failed attempts,
error messages, and unrelated code accumulate in the context window. Once polluted,
the model keeps referencing bad context and cannot escape it.

Ralph's solution: **context is not state**. State lives in files and git. Context is
fresh every iteration.

```
while not tests_pass and iteration < max:
    spec = read_from_disk()      # picks up mid-run edits ("signs")
    agent.run(spec, last_test_output)
    result = run_test_command()
    if result.exit_code == 0: done
```

Three things make this work:

**1. Fresh context per iteration**
Each call to the agent is stateless. Only the spec + the last test output go in.
No accumulating conversation history. The model stays sharp regardless of how many
iterations have run.

**2. Subagents hold heavy context**
The operator/agent has full tool access: read files, write files, run commands,
call subagents. All the heavy work (read 40 files, analyse a codebase) happens
inside the agent's own tool calls. The Ralph loop only sees the final response,
not the intermediate tool outputs.

**3. Test as backpressure**
The test command exit code is the *only* acceptance criterion. The agent doesn't
decide when it's done — the tests do. This prevents the classic LLM failure mode
of declaring success before actually succeeding. Tests are derived from acceptance
criteria in the spec. Can't claim done without passing the tests that prove it.

---

## The Wiggam Extension

BeigeBox adds a **planning phase** before the Ralph loop: **Chief Wiggam**.

The problem Wiggam solves: if the spec is too complex, Ralph will fail or drift.
A goal like "build a caching layer" is not a Ralph-ready task. A task like
"add `get(key)` method to `cache.py` that returns None on miss" is.

Wiggam's **granularity gate**: *"Could Ralph handle this step on his own?"*

**Planning loop:**
```
Round 1:  Wiggam reads goal → proposes tasks + acceptance criteria + test command
Round N:  Officers (other models) critique each task → vote simple_enough / too_complex
          Wiggam synthesizes feedback → refines failing tasks
          Loop until consensus or round cap
```

Wiggam model = the planner. Officer models = the critics. Officers run in parallel.
Consensus = majority of officers mark every task `simple_enough`.

Output: a `PROMPT.md`-ready spec + a `test_cmd` that verifies the whole goal.
User can review and edit before hitting "Approve & Run Ralph".

---

## BeigeBox Implementation

### Files

| File | What it does |
|---|---|
| `beigebox/agents/ralph_orchestrator.py` | `RalphOrchestrator` — the loop engine |
| `beigebox/agents/wiggam_planner.py` | `WiggamPlanner` — consensus planning |
| `beigebox/main.py` | API endpoints for both |
| `beigebox/web/index.html` | Harness tab, "⚡ Ralph" mode + "🚔 Wiggam" planning room |

### API Endpoints

```
POST /api/v1/harness/ralph          Run the autonomous loop (SSE stream)
POST /api/v1/harness/wiggam         Run the planning phase (SSE stream)
POST /api/v1/harness/{run_id}/inject  Inject a steering message mid-run
```

### Ralph endpoint body

```json
{
  "spec_path":      "/path/to/PROMPT.md",   // reloaded each iteration (hot edits)
  "spec_inline":    "...",                  // OR inline spec text
  "test_cmd":       "pytest tests/ -x",     // exit 0 = pass, triggers finish
  "working_dir":    "/path/to/project",     // cwd for test_cmd (default: server cwd)
  "max_iterations": 20,
  "model":          "qwen3:14b"             // optional override
}
```

`spec_path` and `spec_inline` are mutually exclusive. `spec_path` wins if both set.
If `spec_path` is used, the file is re-read from disk before *each* iteration —
edit the file mid-run to steer the agent without restarting.

### Wiggam endpoint body

```json
{
  "goal":           "Add a rate-limiting middleware to the FastAPI app",
  "wiggam_model":   "qwen3:14b",
  "officer_models": ["llama3.2:3b", "qwen3:8b"],
  "max_rounds":     5
}
```

Officers default to `[wiggam_model]` if not specified (self-critique).

### SSE events

**Ralph events:**
```
start           {run_id, spec_path, test_cmd, max_iterations, model, working_dir}
iteration_start {iteration, spec_preview, remaining}
agent_chunk     {iteration, chunk}            streaming tokens as they arrive
agent_done      {iteration, response, latency_ms}
test_run        {iteration, cmd}
test_result     {iteration, exit_code, passed, stdout, stderr, latency_ms}
injected        {iteration, message}          steering message was picked up
finish          {iterations, passed, capped, answer}
error           {message}
```

**Wiggam events:**
```
start           {run_id, goal, wiggam_model, officer_models, max_rounds}
wiggam_plan     {round, reasoning, test_cmd, tasks, plan_md}
officer_vote    {round, officer_model, votes, overall_feedback, test_cmd_ok}
consensus       {round, passed, failing_tasks, task_count}
finish          {spec_md, test_cmd, tasks, rounds, consensus}
error           {message}
```

### Steering (inject mid-run)

Both Ralph and Group Chat share the same inject endpoint:

```
POST /api/v1/harness/{run_id}/inject
{"message": "focus on the cache invalidation path first"}
```

Injected messages are collected between iterations, included in the next prompt
under a "Steering Instructions (added mid-run)" section, then cleared.
Equivalent to editing PROMPT.md — they steer the next iteration without restarting.

---

## RalphOrchestrator internals

### Prompt construction (`_build_prompt`)

Each iteration prompt contains exactly:
1. `# Autonomous Development Task — Iteration N of MAX`
2. The full spec text (from disk or inline)
3. Last test run result (exit code + truncated stdout/stderr, last 2000 chars)
4. Steering instructions (if any injected since last iteration)
5. Working directory + test command
6. "Do not over-explain" instructions

Deliberately compact. Heavy work goes inside the agent's tool calls.

### Agent call (`_call_operator`)

Calls `POST /v1/chat/completions` directly on the backend URL (bypasses proxy
pipeline — no routing, no hooks, no cache). Temperature 0.2. Streams tokens
via SSE. Timeout 300s.

System prompt: "autonomous software development agent running in a loop...
Be decisive and focused — do not ask clarifying questions."

### Test execution (`_run_tests`)

Runs `test_cmd` via `subprocess.run(shell=True)` in `working_dir`.
Timeout 120s. Returns `{exit_code, passed, stdout, stderr, latency_ms}`.
Runs in a thread pool executor (`loop.run_in_executor`) to avoid blocking async.
stdout/stderr truncated to last 4000 chars before being passed back to the agent.

### Iteration loop

```python
for iteration in 1..max_iterations:
    drain injection_queue → collect steering messages
    reload spec from disk
    yield iteration_start event
    stream agent call → yield agent_chunk events
    run tests (in executor) → yield test_result event
    if passed: yield finish(passed=True); return
    sleep(0.5)
yield finish(passed=False, capped=True)
```

---

## WiggamPlanner internals

### System prompts

**Wiggam** is prompted as "Chief Clancy Wiggam" who has learned one lesson:
"A task is only ready to execute if Ralph could do it." Emphasises atomic,
testable, unambiguous, small tasks. Instructed to never argue with officers —
just make tasks smaller.

**Officers** are prompted as "senior software engineers reviewing a task breakdown."
A task is too_complex if it requires architectural decisions, touches > ~3 files,
has multiple acceptance criteria, or a junior would need clarifying questions.
Officers are instructed to be honest, not rubber-stamp to be polite.

### JSON protocol

All calls use `_call_json()`: non-streaming, parses JSON from response,
strips markdown code fences. Temperature 0.4 for Wiggam (creative planner),
0.2 for officers (conservative critics).

### Consensus check

Officers vote per task (`simple_enough: true/false`). Consensus = no task has
majority `false` votes across all officers. If any task fails majority vote,
Wiggam refines that task next round.

### `_build_spec_md`

Converts the final agreed plan into a markdown document suitable for PROMPT.md:
- Goal statement
- Task list with `task_id`, `title`, `description`, `files_affected`, `acceptance_criterion`
- Constraints section (complete tasks in order, don't touch unlisted files)
- Verification command

This is what gets passed to Ralph's inline spec when user clicks "Approve & Run".

---

## Web UI flow (Harness tab, Ralph mode)

1. Click **⚡ Ralph** mode button in Harness tab
2. Fill in **Spec** (file path or inline textarea) and **Test command**
3. Optionally: click **🚔 Plan** to run Wiggam first
   - Wiggam planning room slides open
   - Shows each round: Wiggam's task table, officer votes per task, consensus pass/fail
   - When consensus reached (or cap hit), "✓ Approve & Run Ralph" button appears
   - Clicking it populates the inline spec + test cmd fields and fires Ralph
4. Or skip Wiggam and click **⚡ Run** directly
5. Two-panel output:
   - Left panel: log stream (iteration starts, test pass/fail, inject events)
   - Grid: one card per iteration, streaming tokens as they arrive
   - Each card shows the agent's response + test result (green ✓ / red ✗)
6. **◼ Stop** cancels the loop immediately
7. **Inject bar**: type a steering message → sent to active run via inject endpoint

---

## Design decisions specific to BeigeBox

### Why not use the operator agent for the inner call?

The operator's JSON tool-loop adds overhead and requires the model to emit
structured JSON on every turn. Ralph's inner call is a raw streaming chat —
simpler, faster, and the agent decides its own output format. The operator IS
available as a tool *inside* the Ralph agent's loop if it needs to delegate
subtasks.

### Why `spec_path` reloads every iteration?

This is the "signs" pattern from the original Ralph technique. You can edit
PROMPT.md mid-run (add a constraint, clarify a task, mark a task done) and
the next iteration picks it up without restart. Particularly useful for long
runs where you want to steer without killing progress.

### Test command timeout is 120s

Long enough for most test suites. Short enough to detect hangs. If your tests
take longer, run a subset per iteration and let Ralph accumulate passes.

### `MAX_TEST_OUTPUT_CHARS = 4000`

The last 4000 chars of test output are passed back to the agent. For most
test failures this captures the relevant error. Truncation is tail-biased
(last N chars) because test failures appear at the end of pytest output.

### Wiggam officers run in parallel

`asyncio.gather(*vote_tasks)` — all officers critique the same plan simultaneously.
This keeps planning rounds fast regardless of how many officers are configured.
Failed officer calls are silently skipped (not a hard error).

### No spec required for `test_cmd`

If `test_cmd` is empty, `_run_tests()` returns `exit_code=0` immediately.
This lets you run Ralph as a "keep iterating until agent says done" loop
without external test infrastructure.

---

## Usage patterns

### Simple: iterate on a known task

```
Spec (inline):
  Fix the bug in beigebox/cache.py where keys with unicode characters
  cause a KeyError in SemanticCache.lookup(). The fix should handle
  unicode keys gracefully and return None on miss.

Test cmd:  pytest tests/test_cache.py -x -q
Max iter:  10
```

### Full pipeline: goal → Wiggam plan → Ralph loop

1. Type goal in spec textarea
2. Add officer models (pick 1-2 small/fast models)
3. Click 🚔 Plan → wait for consensus
4. Review the task breakdown, edit spec_md if needed
5. Click ✓ Approve & Run Ralph

### Steer mid-run

While Ralph is running at iteration 7/20:
```
inject: "skip task 3, it's already done — focus on task 4"
```
Ralph picks it up at the start of iteration 8.

---

## Known limitations

- Ralph calls the backend directly (bypasses BeigeBox proxy pipeline) — no
  routing rules, semantic cache, or hooks apply to the inner agent calls
- `subprocess.run(shell=True)` on the test command — be careful with untrusted
  spec inputs; this runs with the BeigeBox server's permissions
- No persistent run storage for Ralph (unlike orchestrator runs) — events are
  streamed and not stored to SQLite
- The 300s agent timeout is a hard limit; very large codebases with many file
  reads may need the model to be faster or the spec to be smaller tasks
