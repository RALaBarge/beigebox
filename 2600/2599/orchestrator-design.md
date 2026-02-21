# Orchestrator Design (v0.6.0)

## Overview

The **Orchestrator** allows the Operator agent to spawn parallel LLM tasks for complex problems that benefit from divide-and-conquer approaches.

## Problem Statement

Some tasks benefit from parallel processing:
- Analyzing multiple documents independently, then synthesizing
- Getting multiple model perspectives on the same question
- Breaking down large problems into smaller, independent sub-tasks

Without orchestration, everything runs sequentially (slow).

## Design Decisions

### 1. Where It Lives
- **Tool in operator.py**: Registered as a LangChain Tool
- **Name**: `parallel_operator` or `orchestrate`
- **Interface**: JSON plan → parallel execution → concatenated results

### 2. Inputs & Outputs

**Input** (JSON):
```json
[
  {"model": "code", "prompt": "Analyze algorithm complexity"},
  {"model": "large", "prompt": "Explain implications"},
  {"model": "fast", "prompt": "Summarize findings"}
]
```

**Output**:
```
[Response from task 1]

[Response from task 2]

[Response from task 3]
```

### 3. Limits & Constraints

```yaml
orchestrator:
  max_parallel_tasks: 5      # Hard cap
  task_timeout_seconds: 120  # Per task
  total_timeout_seconds: 300 # Overall
```

- No task takes >120s
- No orchestration takes >300s
- Max 5 simultaneous tasks

### 4. Error Handling

If one task fails:
- Log the failure
- Include error message in results
- Continue with other tasks
- User gets partial results + errors

## Implementation Notes

### Code Structure
```python
class ParallelOrchestrator:
    async def run(self, plan: list[dict]) -> str:
        """
        Execute parallel tasks.
        plan = [{"model": "...", "prompt": "..."}, ...]
        Returns concatenated responses.
        """
```

### Backend Interaction

Uses existing `proxy.forward_chat_completion()`:
```python
tasks = [
    proxy.forward_chat_completion({
        "model": task["model"],
        "messages": [{"role": "user", "content": task["prompt"]}],
        "stream": False
    })
    for task in plan
]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

### Logging

Logs to wiretap:
- Plan received
- Tasks spawned
- Task completion/failure
- Total latency
- Results synthesized

## API Endpoints

**Via Operator Tool** (primary):
- Operator calls `parallel_operator` tool internally
- Input: JSON plan
- Output: Concatenated results

**Via HTTP** (future):
- `POST /api/v1/orchestrator` with plan JSON
- Returns task results

## Configuration

```yaml
orchestrator:
  enabled: false
  max_parallel_tasks: 5
  task_timeout_seconds: 120
  total_timeout_seconds: 300
```

## Example Usage

```
User: z: operator "Analyze these 3 papers in parallel"

Operator:
1. Parses request
2. Creates plan:
   [
     {"model": "code", "prompt": "Paper 1 - focus on methods"},
     {"model": "code", "prompt": "Paper 2 - focus on results"},
     {"model": "large", "prompt": "Paper 3 - focus on conclusions"}
   ]
3. Spawns 3 concurrent requests
4. Waits for all to complete
5. Concatenates results
6. Synthesizes into final answer

Response: [Combined analysis from all 3 papers]
```

## Testing

- [ ] Happy path: all tasks succeed
- [ ] Partial failure: one task fails, others continue
- [ ] Timeout: one task exceeds task timeout
- [ ] Overload: max_parallel_tasks respected
- [ ] Total timeout: orchestration exceeds total timeout

## Future Enhancements

- Task dependencies (run B only after A)
- Task weighting (important tasks run on large model)
- Result aggregation strategies (vote, consensus, etc.)
- Task chaining within orchestration
