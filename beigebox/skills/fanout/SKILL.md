---
name: fanout
version: 1
description: Use when the user wants to fan-out / distribute / split / parallelize a list of inputs across multiple model calls so a single oversized prompt doesn't blow the context or token budget — especially relevant for reasoning models (trinity-large-thinking, deepseek-r1, o3) whose internal reasoning consumes the budget before any visible output. Takes a list of items, a per-item prompt template, and a model; runs N parallel calls against the BeigeBox proxy with a configurable concurrency cap; optionally reduces the responses with a final merge call. Item sources are file (one per line), JSON list, glob (each match becomes {path, contents}), or stdin.
---

# fanout

`scripts/fanout.sh` (or `python3 -m beigebox.skills.fanout`) — fan a list of inputs out to N parallel model calls and optionally reduce the responses with a final merge prompt. Solves the "one prompt is too big for the model's reasoning budget" failure mode (e.g. asking trinity-large-thinking to review 13 files at once and getting `finish_reason: length` with zero visible output).

## When to invoke

- User asks to "fan out / distribute / split / parallelize" a task across multiple model calls
- A reasoning model returned no content (`finish_reason: length`, all tokens consumed by internal reasoning)
- The natural unit of work is per-file / per-function / per-record and a single combined prompt would be too large
- An audit / review / triage task has a list of inputs and the user wants per-item findings + an aggregate summary

## Usage

```bash
# review every Python file under src/ in parallel, then merge findings
scripts/fanout.sh \
  --items-glob 'src/**/*.py' \
  --template 'Review this file for security bugs.\n\n{item.path}:\n{item.contents}' \
  --model x-ai/grok-4 \
  --concurrency 4 \
  --reduce 'Merge {count} per-file reviews into a single ranked finding list:\n\n{responses}'

# one-line-per-item from a file, no reduce step
scripts/fanout.sh \
  --items-file urls.txt \
  --template 'Summarize the page at this URL: {item}' \
  --model anthropic/claude-sonnet-4.5

# JSON list of dicts on stdin, dotted-field substitution
echo '[{"name":"alice","role":"sre"},{"name":"bob","role":"swe"}]' | \
  scripts/fanout.sh \
    --items-stdin \
    --template 'Write a one-line bio for {item.name} ({item.role}).' \
    --model x-ai/grok-4

# human-readable summary instead of JSON
scripts/fanout.sh --items-file files.txt --template '...' --model ... --format summary

# write JSON to a file
scripts/fanout.sh --items-file files.txt --template '...' --model ... --out results.json
```

From Python (the import path Trinity-style orchestrators should use):

```python
from beigebox.skills.fanout import fan_out

result = await fan_out(
    items=["file1.py", "file2.py", "file3.py"],
    prompt_template="Review {item} and report bugs.",
    model="arcee-ai/trinity-large-thinking",
    concurrency=3,
    reduce_prompt="Merge {count} reviews:\n\n{responses}",
)
# result["responses"] -> list of {item, content, finish_reason, tokens, error}
# result["reduce"]    -> {content, finish_reason, tokens} | None
# result["stats"]     -> {items, succeeded, failed, total_*_tokens, total_duration_seconds}
```

## Templates

Per-item template substitutions:

| Token        | Resolves to                                                   |
|--------------|---------------------------------------------------------------|
| `{item}`     | The item itself (str directly, or `json.dumps(...)` for dicts)|
| `{item.foo}` | `item["foo"]` for dict items                                  |
| `{index}`    | Zero-based item index                                         |

Reduce template substitutions:

| Token         | Resolves to                                                    |
|---------------|----------------------------------------------------------------|
| `{responses}` | Successful responses joined with `\n\n---\n\n` and numbered    |
| `{count}`     | Number of successful responses                                 |

Unknown placeholders are left as literal `{key}` rather than raising — a typo doesn't kill the run.

## Item sources (pick exactly one)

- `--items-file PATH` — one item per line (whitespace stripped, blanks dropped)
- `--items-glob 'PATTERN'` — each matching path becomes `{"path": ..., "contents": ...}`
- `--items-json PATH` — JSON file with a list (of strings or dicts)
- `--items-stdin` — JSON list on stdin

## Output shape

```json
{
  "responses": [
    {
      "item": "<the original item>",
      "content": "<model output>",
      "finish_reason": "stop",
      "tokens": {"prompt_tokens": 412, "completion_tokens": 1840},
      "duration_seconds": 12.4,
      "model": "x-ai/grok-4-07-09",
      "error": null
    }
  ],
  "reduce": {
    "content": "<merged>",
    "finish_reason": "stop",
    "tokens": {...},
    "duration_seconds": 8.1,
    "error": null
  },
  "stats": {
    "items": 6,
    "succeeded": 6,
    "failed": 0,
    "total_prompt_tokens": 4920,
    "total_completion_tokens": 14110,
    "total_duration_seconds": 18.3
  }
}
```

## Requirements

- `python3` ≥ 3.11 with `httpx` (already a beigebox dependency)
- A reachable OpenAI-compat endpoint — defaults to `http://localhost:1337/v1` (the BeigeBox proxy)

## Behavior notes

- **Per-item failure isolation.** If one call raises, the run continues; the failure is captured in the response dict's `error` field. The CLI exits non-zero only if at least one item failed.
- **Reduce is gated.** The reduce step only fires when every item succeeded. Pass `--reduce-on-partial` to merge whatever came back.
- **Concurrency.** A `Semaphore` caps in-flight calls; default 4. Raise it for cheap models, lower it if your upstream rate-limits.
- **Auth.** The default API key is the literal string `none` — accepted by the BeigeBox proxy, which substitutes the real upstream key from its env. Override with `--api-key` only if you're calling a non-BeigeBox endpoint.
- **Default model handling.** No model default is shipped — you must pass `--model`. Reasoning models route through BeigeBox's request normalizer and pick up `DEFAULT_REASONING_MAX_TOKENS` automatically when `--max-tokens` is omitted.

## Anti-patterns

- Don't use this for **streaming** — fanout collects whole responses. If you need streaming, call the proxy directly per item.
- Don't use this for **stateful conversations** — every per-item call is one-shot. If you need multi-turn dialogue per item, use the Operator agent.
- Don't fan out **a single document** by char-splitting it here. Pre-split with a real chunker (paragraph-aware or semantic) and pass the chunks in as a list. This skill's job is parallelization, not segmentation.
