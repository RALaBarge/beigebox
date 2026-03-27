# ✅ COMPLETE — Reference research. Six-layer architecture patterns noted. No further action required.

Most of them do **not** keep “re-explaining the whole mission” to the bot every loop.

They usually split the problem into a few durable layers:

## What current harnesses tend to do

### 1. Fixed role + mutable task state

The agent keeps a relatively stable identity such as *planner*, *coder*, *reviewer*, or *researcher*, while the changing part lives in state: current objective, subtask list, artifacts produced so far, tool outputs, and stop conditions. LangGraph explicitly frames this as **stateful workflows/agents** with persistence and dynamic tool usage, while AutoGen agents are described as **stateful** and meant to receive **new messages rather than complete history** on each call. ([LangChain Docs][1])

### 2. A loop, not a giant one-shot prompt

Modern harnesses generally run a repeated cycle like:

`task/context -> model call -> tool/action -> observation/result -> next model call`

OpenAI’s Agents SDK documentation describes this directly as an **agent loop** that can call tools over multiple turns, and OpenHands positions its SDK around task planning, decomposition, tool use, and context compression for longer-running coding work. ([OpenAI Developers][2])

### 3. Summaries instead of replaying full transcripts

Once a thread gets long, frameworks usually compress the prior work into a summary or structured state object instead of stuffing every old token back into the model. AutoGen’s sequential chat pattern explicitly passes **summaries from previous tasks** into later tasks, and OpenHands calls out **automatic context compression** as a built-in agentic feature. ([Microsoft GitHub][3])

### 4. Checkpoints / persistence

Serious harnesses persist execution state so the run can resume, be inspected, or branch. LangGraph highlights **persistence** as a core benefit for workflows and agents, and OpenAI’s Agents SDK emphasizes keeping a **full trace** plus built-in **tracing** for debugging what happened. ([LangChain Docs][1])

### 5. Reflection or critique passes

A lot of systems add a second pass that critiques the current output and updates either the plan or the instructions. LangGraph’s memory docs describe “reflection” / meta-prompting as a way for an agent to refine its own instructions based on recent conversation or feedback, rather than changing weights or code. ([LangChain Docs][4])

### 6. Multi-agent handoff when the mode changes

Instead of one bot doing everything, newer frameworks often switch agents when the job changes shape: planner → researcher → implementer → verifier. OpenAI’s Agents SDK treats **handoffs** as a first-class primitive, and AutoGen exposes patterns like group chat, swarm, workflows, memory/RAG, and specialized agents. ([OpenAI Developers][2])

---

## So how do they actually “instruct over iterations”?

Usually one of these four patterns:

### Pattern A: Immutable constitution + rolling scratchpad

* Stable system prompt with rules, style, tool policy, success criteria
* Short “working memory” object updated every turn
* Periodic summarization into a cleaner state blob

This is the most common pattern because it is simple and cheap.

### Pattern B: Plan file / task ledger

The harness maintains a structured artifact such as:

* goal
* constraints
* current step
* completed steps
* failed attempts
* known facts
* open questions
* exit criteria

Then each loop injects that ledger rather than a raw transcript.

This is the pattern to use when you want the bot to feel like it is “staying on the same project” across many iterations.

### Pattern C: Planner-executor-reviewer

One model or role plans, another executes, another critiques. This reduces drift because the executor is not also deciding what “done” means.

### Pattern D: Event-sourced run history

Instead of storing prose summaries only, the harness stores events:

* user instruction received
* plan created
* tool called
* file changed
* test failed
* test passed
* reviewer flagged issue

Then the next iteration reconstructs a compact state view from those events.

That is closer to how robust developer agents and harnesses are trending, because it gives replay, observability, and deterministic debugging. OpenAI’s tracing focus and LangGraph’s persistence both fit this direction. ([LangChain Docs][1])

---

## What they usually avoid

The better tools usually avoid:

### Re-sending the whole transcript forever

This gets expensive, causes drift, and lets early mistakes keep poisoning the run.

### Letting the same free-form prompt mutate uncontrollably

If the agent “learns” by rewriting its own instructions without guardrails, it tends to drift or become self-congratulatory. Reflection is usually bounded and stored separately, not allowed to silently overwrite the whole operating contract. LangGraph’s docs present reflection as a specific memory/instruction update mechanism, not uncontrolled self-redefinition. ([LangChain Docs][4])

### Treating memory as one giant blob

Good harnesses separate:

* conversation history
* task state
* durable preferences/rules
* artifacts/files
* tool results
* evaluation signals

---

## The practical architecture most people end up with

For an autonomous iterative harness, the most stable design is:

1. **Static policy/instruction layer**

   * what the agent is
   * what tools it may use
   * quality bar
   * safety/approval rules

2. **Structured run state**

   * current objective
   * plan
   * step number
   * blockers
   * completion criteria

3. **Artifact store**

   * files, code patches, notes, docs, outputs

4. **Event log / trace**

   * exact actions and observations

5. **Compaction layer**

   * summarizer compresses old context into state updates

6. **Evaluation / critique loop**

   * tests, linters, assertions, or reviewer agent

That is basically the common denominator across current agent frameworks, even though each markets it differently. LangGraph leans graph/state/persistence, AutoGen leans conversable stateful agents and multi-agent patterns, OpenAI leans agent loop/handoffs/guardrails/tracing, and OpenHands leans coding-focused planning plus context compression and strong tool/computer interfaces. ([LangChain Docs][1])

---

## What I would do for your harness

For something BeigeBox-like, I would not let the model “remember the project” by chat history alone.

I would give it these explicit objects each iteration:

```yaml
mission:
  objective: ...
  hard_constraints: [...]
  success_criteria: [...]
run_state:
  current_phase: plan|research|implement|verify
  current_step: ...
  completed_steps: [...]
  blockers: [...]
  next_actions: [...]
artifacts:
  relevant_files: [...]
  latest_outputs: [...]
memory:
  durable_facts: [...]
  recent_summary: ...
control:
  max_iterations: 12
  escalation_rules: [...]
  stop_if:
    - success_criteria_met
    - repeated_failure_on_same_step >= 3
```

Then run:

```text
planner -> executor -> verifier -> state reducer -> next iteration
```

The important part is that the **state reducer** is a first-class component. That reducer decides what survives into the next loop. Without that, “autonomous iteration” usually becomes transcript bloat plus drift.

---

## The real dividing line

The key difference between weak and strong harnesses is this:

* **weak harness**: “Here is the conversation so far, keep going”
* **strong harness**: “Here is the canonical state of the project, here is what changed last turn, and here is the next bounded decision”

That is how they keep a bot working on the same thing over many iterations without it wandering off.

If useful, I can map this into a concrete BeigeBox control-plane design with message schemas for `plan`, `act`, `observe`, `reduce`, and `handoff`.

[1]: https://docs.langchain.com/oss/python/langgraph/workflows-agents "Workflows and agents - Docs by LangChain"
[2]: https://developers.openai.com/tracks/building-agents/ "Building agents"
[3]: https://microsoft.github.io/autogen/0.2/docs/notebooks/agentchat_multi_task_chats/ "Solving Multiple Tasks in a Sequence of Chats | AutoGen 0.2"
[4]: https://docs.langchain.com/oss/python/langgraph/memory "Memory overview - Docs by LangChain"
