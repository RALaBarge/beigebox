# ✅ COMPLETE — Reference research. Autoresearch loop patterns for self-tuning classifier/routing documented. Not yet built as a feature but design is captured.

# autoresearch for beigebox

> Adapted from [karpathy/autoresearch](https://github.com/karpathy/autoresearch) and [hwchase17/autoresearch-agents](https://github.com/hwchase17/autoresearch-agents). The Karpathy Loop applied to BeigeBox middleware optimization.

**Status**: Design document / TODO  
**Date**: 2026-03-15  
**Priority**: High — immediate ROI on routing quality and cost optimization  

---

## The Pattern (TL;DR)

Three primitives make autonomous experiment loops work:

1. **Editable asset** — a single file the agent is permitted to modify. Keeps search space interpretable, every hypothesis is a reviewable diff.
2. **Scalar metric** — a single number that defines "better". Must be computable without human judgment, unambiguous about direction.
3. **Time-boxed cycle** — fixed duration per experiment. Makes every run directly comparable regardless of what changed.

The agent loops: read state → propose change → edit file → commit → run eval → parse score → keep or discard → log → repeat. Git history becomes the experiment journal. A `program.md` file encodes human intent (what to search, what to hold fixed, when to stop) as the control interface.

Karpathy ran 50 ML training experiments overnight on one GPU. Chase adapted it for agent optimization with LangSmith evals. Both produced measurable improvements autonomously.

---

## What BeigeBox Already Has

The pattern maps cleanly because BeigeBox already has most of the infrastructure:

- **Wiretap logging** — every request/response with per-stage timing. Source of labeled data for holdout sets.
- **Cost tracking** — per-request dollar costs from OpenRouter. Half of any cost/quality composite metric.
- **LLM judge** — ensemble voting with judge LLM already exists. Quality scoring capability is built in.
- **Config-driven everything** — features are flags, not code. Editable assets are config files, not application code.
- **SQLite + WAL** — durable storage for results logging. Append-only audit log pattern already on the roadmap.
- **system_context.md hot-reload** — the `program.md` pattern already exists in spirit.
- **Conversation storage** — JSONL export means eval datasets can be built from production traffic.
- **Git-based workflow** — BeigeBox is already versioned; experiment branches are natural.

---

## Target Domains

### Domain 1: Routing Classifier Tuning (HIGHEST PRIORITY)

**The problem**: Four-way embedding classification (simple/complex/code/creative) determines which model handles every request. Thresholds and example embeddings were set by hand. Small improvements here compound across every single query.

| Component | Value |
|---|---|
| **Editable asset** | `routing_config.yaml` — similarity thresholds, category example texts, fallback rules, confidence floors |
| **Scalar metric** | Classification accuracy against labeled holdout set (from wiretap logs) |
| **Fixed constraints** | Holdout dataset, embedding model (nomic-embed-text), ChromaDB backend, evaluation script |
| **Time budget** | ~30s per experiment (embed + classify holdout set + score) |
| **Experiments/hour** | ~120 (this is fast — no LLM inference in the loop) |

**Holdout set construction**: Pull N queries from wiretap logs. Human labels each with correct routing category. Export as JSON. This is the one-time human investment. 50-100 labeled examples is enough to start.

**What the agent can change**: similarity thresholds per category, number and content of example texts per category, distance metric weights, confidence floor before fallback, fallback routing rules, category boundary overlap handling.

**What the agent cannot change**: the embedding model itself, the holdout dataset, the evaluation script, the ChromaDB schema.

### Domain 2: System Context Optimization

**The problem**: `system_context.md` shapes every response but was written once by hand. An autonomous loop can grind through prompt variations and measure downstream quality.

| Component | Value |
|---|---|
| **Editable asset** | `system_context.md` — the system prompt injected into every request |
| **Scalar metric** | LLM judge quality score (averaged across eval set) |
| **Fixed constraints** | Eval prompt set, judge model + judge prompt, scoring rubric |
| **Time budget** | ~2-5min per experiment (depends on eval set size and model latency) |
| **Experiments/hour** | ~12-30 |

**Eval set construction**: Curate 20-30 representative prompts spanning simple/complex/code/creative categories. Include expected quality characteristics (not exact answers — judge scores against rubric).

**What the agent can change**: system prompt text, structure, tone instructions, formatting guidance, persona definition, constraint language, few-shot examples within the prompt.

**What the agent cannot change**: eval prompt set, judge model, judge prompt, scoring rubric, the BeigeBox routing/proxy layer.

### Domain 3: Ensemble Voting Configuration

**The problem**: Judge LLM parameters, voting thresholds, and candidate selection rules are all config. Quality-per-dollar is the metric that matters for consulting clients.

| Component | Value |
|---|---|
| **Editable asset** | `ensemble_config.yaml` — judge prompt, voting threshold, candidate count, model selection per slot, temperature per candidate |
| **Scalar metric** | Composite: `(quality_score * weight_q) - (cost_per_query * weight_c)` |
| **Fixed constraints** | Eval prompt set, quality judge, cost tracking API |
| **Time budget** | ~3-5min per experiment (multiple LLM calls per eval query) |
| **Experiments/hour** | ~12-20 |

### Domain 4: Provider Routing Cost/Quality

**The problem**: Which OpenRouter models handle which query classes? Currently manual mapping. The loop can find the Pareto frontier of cost vs. quality per category.

| Component | Value |
|---|---|
| **Editable asset** | `provider_routing.yaml` — model assignment per query class, fallback chains, cost ceilings |
| **Scalar metric** | Quality-adjusted cost: `quality_score / cost_per_query` (higher is better) |
| **Fixed constraints** | Eval prompt set, quality judge, available models list |
| **Time budget** | ~5min per experiment |
| **Experiments/hour** | ~12 |

**Consulting value**: This produces a demonstrable, auditable optimization trail. Client sees git log of "we tried X models on Y query types, here's what saved money without losing quality." That's the sales pitch materialized.

---

## Architecture

### File Structure

```
beigebox/
├── autoresearch/
│   ├── program.md              # Agent instructions (human-edited control interface)
│   ├── run_eval.py             # Evaluation harness (FIXED during loop)
│   ├── results.tsv             # Experiment log (append-only)
│   ├── domains/
│   │   ├── routing/
│   │   │   ├── routing_config.yaml      # EDITABLE ASSET
│   │   │   ├── holdout_dataset.json     # FIXED
│   │   │   ├── eval_routing.py          # FIXED
│   │   │   └── program_routing.md       # Domain-specific agent instructions
│   │   ├── system_context/
│   │   │   ├── system_context.md        # EDITABLE ASSET
│   │   │   ├── eval_prompts.json        # FIXED
│   │   │   ├── eval_context.py          # FIXED
│   │   │   └── program_context.md       # Domain-specific agent instructions
│   │   ├── ensemble/
│   │   │   ├── ensemble_config.yaml     # EDITABLE ASSET
│   │   │   ├── eval_prompts.json        # FIXED
│   │   │   ├── eval_ensemble.py         # FIXED
│   │   │   └── program_ensemble.md      # Domain-specific agent instructions
│   │   └── provider_routing/
│   │       ├── provider_routing.yaml    # EDITABLE ASSET
│   │       ├── eval_prompts.json        # FIXED
│   │       ├── eval_providers.py        # FIXED
│   │       └── program_providers.md     # Domain-specific agent instructions
│   └── tools/
│       ├── build_holdout.py            # Extract + label queries from wiretap logs
│       ├── export_results.py           # Export results.tsv to various formats
│       └── compare_experiments.py      # Diff two experiment branches
```

### The Loop (adapted from Chase)

```
┌──────────────────────────────────────────────────────────┐
│                  BEIGEBOX EXPERIMENT LOOP                 │
│                                                          │
│  1. Read editable asset + results.tsv so far             │
│  2. Propose a change (thresholds, prompts, models, etc.) │
│  3. Edit the editable asset (ONLY this file)             │
│  4. git commit -m "experiment: <description>"            │
│  5. Run evaluation: python run_eval.py <domain>          │
│     > eval.log 2>&1                                      │
│  6. Parse score from eval output                         │
│  7. If improved → keep commit (advance branch)           │
│     If worse   → git reset --hard HEAD~1 (discard)       │
│  8. Append result to results.tsv                         │
│  9. LOOP FOREVER until human interrupts                  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### Evaluation Harness Design (run_eval.py)

Unlike Chase's version which depends on LangSmith, BeigeBox's eval harness is **self-contained** — it uses BeigeBox's own infrastructure:

```python
# Pseudocode — actual implementation TBD

def run_eval(domain: str, config_path: str, dataset_path: str) -> dict:
    """
    Generic eval runner. Each domain provides:
    - A config loader (reads the editable asset)
    - A dataset loader (reads the fixed holdout set)
    - An evaluator function (scores predictions against ground truth)
    """
    config = load_config(domain, config_path)
    dataset = load_dataset(dataset_path)
    
    scores = []
    for example in dataset:
        prediction = run_prediction(domain, config, example["inputs"])
        score = evaluate(domain, prediction, example["outputs"])
        scores.append(score)
    
    return {
        "overall_score": mean(scores),
        "num_examples": len(dataset),
        "num_errors": count_errors(scores),
        "per_category": breakdown_by_category(scores),  # BeigeBox-specific
        "cost_total": sum_costs(scores),                  # BeigeBox-specific
    }
```

**Key difference from Chase**: No external eval service dependency. BeigeBox already has the LLM judge, cost tracking, and logging. The eval harness calls BeigeBox's own API endpoints or imports its modules directly.

### Scoring Output Format

```
---
overall_score: 0.873000
routing_accuracy: 0.920000
avg_quality: 0.850000
avg_cost_per_query: 0.002340
quality_per_dollar: 363.247863
num_examples: 50
num_errors: 0
domain: routing
```

### Results TSV Format

```
commit	overall_score	accuracy	quality	cost	status	description
a1b2c3d	0.873000	0.920000	0.850000	0.002340	keep	baseline
b2c3d4e	0.891000	0.940000	0.860000	0.002280	keep	raised code threshold from 0.7 to 0.75
c3d4e5f	0.865000	0.900000	0.850000	0.002400	discard	lowered creative threshold — too many false positives
d4e5f6g	0.000000	0.000000	0.000000	0.000000	crash	invalid yaml syntax
```

---

## Implementation Plan

### Phase 0: Holdout Set Construction (HUMAN WORK)

This is the irreducible human contribution. No way around it.

1. **Export wiretap logs** — Pull last N days of queries from SQLite.
2. **Sample diverse queries** — Stratified sample across routing categories. Aim for 50-100.
3. **Label ground truth** — For each query, record: correct routing category, expected quality characteristics, whether tools should be used.
4. **Export as JSON** — Format matching the eval harness expectations.

Time estimate: 2-4 hours of focused labeling work. This is the "writing `program.md`" equivalent — highest-leverage human time investment.

### Phase 1: Routing Classifier Loop (FIRST TARGET)

Why first: fastest cycle time (~30s), clearest metric (accuracy), most immediate ROI (affects every request), no LLM inference in the eval loop (cheap to run).

**Deliverables:**
- [ ] `autoresearch/domains/routing/eval_routing.py` — loads routing config, classifies holdout set, scores accuracy
- [ ] `autoresearch/domains/routing/holdout_dataset.json` — labeled query set
- [ ] `autoresearch/domains/routing/routing_config.yaml` — extracted from current BeigeBox config as standalone editable asset
- [ ] `autoresearch/domains/routing/program_routing.md` — agent instructions for routing experiments
- [ ] `autoresearch/run_eval.py` — generic harness that dispatches to domain-specific evaluators
- [ ] `autoresearch/tools/build_holdout.py` — extracts queries from wiretap DB, outputs unlabeled JSON for human annotation

### Phase 2: System Context Loop

**Deliverables:**
- [ ] `autoresearch/domains/system_context/eval_context.py` — sends eval prompts through BeigeBox with modified system_context.md, judges responses
- [ ] `autoresearch/domains/system_context/eval_prompts.json` — curated prompt set with quality rubric
- [ ] `autoresearch/domains/system_context/program_context.md` — agent instructions

### Phase 3: Provider Routing Loop

**Deliverables:**
- [ ] `autoresearch/domains/provider_routing/eval_providers.py` — routes eval prompts through different model assignments, measures quality + cost
- [ ] `autoresearch/domains/provider_routing/provider_routing.yaml` — model-to-category mapping as editable asset
- [ ] `autoresearch/domains/provider_routing/program_providers.md` — agent instructions

### Phase 4: Ensemble Voting Loop

**Deliverables:**
- [ ] `autoresearch/domains/ensemble/eval_ensemble.py` — runs ensemble pipeline with modified config, measures quality-per-dollar
- [ ] `autoresearch/domains/ensemble/ensemble_config.yaml` — judge prompt + voting params as editable asset
- [ ] `autoresearch/domains/ensemble/program_ensemble.md` — agent instructions

---

## program.md Template (BeigeBox-Specific)

This is the human-agent interface. Adapted from Chase's version with BeigeBox specifics:

```markdown
# autoresearch: beigebox routing optimization

This is an autonomous experiment loop to optimize BeigeBox's routing classifier.

## Setup

1. **Agree on a run tag**: propose a tag based on today's date (e.g. `mar15`).
   Branch: `autoresearch/routing/<tag>`
2. **Create the branch**: `git checkout -b autoresearch/routing/<tag>` from current main.
3. **Read the in-scope files**:
   - `autoresearch/domains/routing/routing_config.yaml` — the file you modify
   - `autoresearch/domains/routing/eval_routing.py` — evaluation harness (DO NOT MODIFY)
   - `autoresearch/domains/routing/holdout_dataset.json` — test cases (DO NOT MODIFY)
4. **Run the baseline**: `python autoresearch/run_eval.py routing > eval.log 2>&1`
5. **Initialize results.tsv** with header and baseline entry.

## Experimentation

**What you CAN do:**
- Modify `routing_config.yaml` — similarity thresholds, category example texts,
  confidence floors, fallback rules, distance metric weights. Everything is fair game.

**What you CANNOT do:**
- Modify `eval_routing.py`, `holdout_dataset.json`, or any BeigeBox source code.
- Change the embedding model.

**The goal: highest `overall_score` (routing accuracy).**

## The Experiment Loop

LOOP FOREVER:

1. Read current routing_config.yaml + results.tsv
2. Propose a change. Consider:
   - Adjusting similarity thresholds per category
   - Adding/removing/editing example texts for categories
   - Changing confidence floor before fallback
   - Modifying fallback routing rules
   - Tuning distance metric weights
   - Combining previous near-misses
3. Edit routing_config.yaml
4. git commit
5. Run: `python autoresearch/run_eval.py routing > eval.log 2>&1`
6. Parse: `grep "^overall_score:" eval.log`
7. If improved → keep commit
   If worse → git reset --hard HEAD~1
8. Log to results.tsv
9. NEVER STOP until interrupted

## Ideas to try

- Raise/lower individual category thresholds by 0.01-0.05 increments
- Add more example texts for categories with low accuracy
- Remove noisy/ambiguous example texts
- Adjust the boundary between simple and complex queries
- Try asymmetric thresholds (tighter for expensive categories, looser for cheap)
- Experiment with the confidence floor — too low sends garbage to expensive models,
  too high sends everything to fallback
```

---

## Key Differences from Chase's Implementation

| Aspect | Chase (autoresearch-agents) | BeigeBox Adaptation |
|---|---|---|
| **Eval service** | LangSmith (external SaaS) | Self-contained (BeigeBox's own LLM judge + cost tracking) |
| **Editable asset** | `agent.py` (Python code) | Domain-specific config files (YAML/Markdown) |
| **Agent framework** | Any (LangGraph, OpenAI SDK, etc.) | BeigeBox's own routing/proxy/ensemble infrastructure |
| **Observability** | LangSmith traces | Wiretap logging (already built) |
| **Metric** | `overall_score` (quality only) | Composite `quality_score / cost` or domain-specific accuracy |
| **Dataset** | JSON + LangSmith persistent dataset | JSON holdout sets built from wiretap production logs |
| **Multiple domains** | Single agent optimization | Four domains (routing, system context, ensemble, provider routing) |
| **Cycle time** | ~2-5 min (LLM inference) | ~30s (routing) to ~5min (ensemble) depending on domain |

---

## Reference: Chase's Source Files

### agent.py (the editable asset pattern)

The key contract is that the editable file exposes a function that the eval harness can call. In Chase's case:

```python
def run_agent_with_tools(question: str) -> dict:
    """Returns {"response": str, "tools_used": list}"""
```

For BeigeBox, the equivalent per domain:
- **Routing**: config file loaded by eval script, no function contract needed
- **System context**: `system_context.md` loaded by BeigeBox at startup, eval hits the API
- **Ensemble**: config loaded by eval script, calls ensemble pipeline internally
- **Provider routing**: config loaded by eval script, routes through BeigeBox API

### run_eval.py (the fixed evaluation harness)

Chase's evaluator uses three scoring functions:
1. `correctness_evaluator` — LLM-as-judge, binary (correct/incorrect)
2. `helpfulness_evaluator` — LLM-as-judge, binary (helpful/unhelpful)
3. `tool_usage_evaluator` — code-based, checks if tools were used when expected

Overall score = mean of all three averages.

For BeigeBox routing, the evaluator is simpler and faster:
```python
def routing_evaluator(prediction, expected) -> dict:
    """Code-based: did the classifier pick the right category?"""
    return {"score": 1 if prediction["category"] == expected["category"] else 0}
```

No LLM judge needed for routing — it's a deterministic classification check. This is why routing should be Phase 1: fastest, cheapest, most experiments per hour.

### program.md (the human-agent interface)

Chase's program.md structure:
1. **Setup** — branch creation, file reading, env verification, baseline run
2. **Experimentation** — what can/cannot change, goal metric, cost as soft constraint, simplicity criterion
3. **Output format** — how to parse eval results
4. **Logging** — TSV format with columns
5. **The loop** — step-by-step with keep/discard/crash handling
6. **NEVER STOP** — explicit instruction to run indefinitely
7. **Ideas to try** — seed the search space

This structure transfers directly. Each BeigeBox domain gets its own program.md with domain-specific ideas and constraints.

### dataset.json (the fixed evaluation set)

Chase's format:
```json
[
  {
    "inputs": {"question": "..."},
    "outputs": {"answer": "...", "expected_tool_use": true/false}
  }
]
```

BeigeBox routing format:
```json
[
  {
    "inputs": {"query": "write me a python function to parse CSV files"},
    "outputs": {"category": "code", "confidence_floor": 0.7}
  }
]
```

BeigeBox system context format:
```json
[
  {
    "inputs": {"prompt": "explain quantum computing simply"},
    "outputs": {
      "quality_rubric": "clear, accurate, appropriate depth",
      "category": "complex"
    }
  }
]
```

---

## Running the Loop

### With Claude Code (recommended)

```bash
cd beigebox/
# Point Claude Code at the program.md and let it go
claude "Read autoresearch/domains/routing/program_routing.md and follow the instructions. Run autonomously."
```

### With Any Coding Agent

The loop is agent-agnostic. Any tool that can edit files, run shell commands, and parse output works: Claude Code, Cursor, Codex, Aider, etc.

### Overnight Run Expectations

| Domain | Cycle Time | Experiments/Hour | Overnight (8h) |
|---|---|---|---|
| Routing | ~30s | ~120 | ~960 |
| System Context | ~3min | ~20 | ~160 |
| Provider Routing | ~5min | ~12 | ~96 |
| Ensemble | ~5min | ~12 | ~96 |

Routing alone could explore nearly 1000 configurations overnight. On a 4070Ti Super this is essentially free — the embedding inference is trivial.

---

## Open Questions

1. **Hybrid BM25 + vector search interaction**: If we run the routing autoresearch loop *after* the Embex migration and hybrid search implementation, the holdout set needs to account for the new retrieval characteristics. Sequence matters.

2. **Metric gaming**: Goodhart's Law is real. If the routing loop optimizes for holdout accuracy and the holdout set isn't representative of production traffic, we get a classifier that aces the test but fails in the wild. Mitigation: periodic holdout refresh from recent wiretap logs.

3. **Multi-objective optimization**: Provider routing needs quality AND cost. A single scalar requires choosing weights. Who decides the weight? The consulting client, ideally — this becomes a configurable parameter in program.md.

4. **LLM judge consistency**: System context and ensemble eval depend on LLM judge scores. Judge variance across runs adds noise. Mitigation: use temperature=0, run each eval example 3x and average, or use a cheaper deterministic proxy metric where possible.

5. **Interaction effects**: Routing changes affect which model sees which query, which affects quality scores, which affects ensemble behavior. Running domains independently might miss these interactions. A full-stack eval that scores the entire pipeline is the eventual goal, but domain-specific loops are the right starting point.

---

## Next Steps

1. **Attach codebase zip** in next session for implementation
2. **Build holdout set** (Phase 0) — this is the human bottleneck
3. **Implement routing eval harness** (Phase 1) — fastest path to first overnight run
4. **Write program_routing.md** — seed the search space
5. **Run first overnight experiment** — validate the pattern on BeigeBox
6. **Iterate on program.md** — the meta-optimization (improving the instructions that drive the loop)
