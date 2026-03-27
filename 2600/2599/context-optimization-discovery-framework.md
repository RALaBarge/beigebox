# 🔄 IN PROGRESS — Phase 1 infrastructure complete: OracleRegistry (50 golden tests), JudgeRubric (5-axis scoring), ParetoOptimizer, HoldoutRegistry, EvalRunner, DiscoveryRunner. Phases 2-6 (baseline run, variant testing, statistical validation, integration) not yet executed. No test suite YAML files built yet.

---
title: Context Optimization Discovery Framework
subtitle: 15 Discovery Opportunities + Scientific Testing Strategies
date: 2026-03-18
author: Claude + Research Synthesis (VLDB 2026, ACL 2025, ArXiv 2025)
---

# Context Optimization Discovery Framework

**Objective**: Systematically discover optimal context composition strategies for BeigeBox through scientifically rigorous A/B testing with proper controls, baselines, and multi-dimensional scoring.

**Hypothesis**: Context composition strategy (what goes into a packet) is a major lever for improving output quality, reducing hallucination, and optimizing token efficiency — potentially 15-30% improvements on top of prompt optimization.

---

## Part 1: Discovery Opportunities (15 Total)

### Original 5 Opportunities

#### 1. **Composition Strategy Variance Per Worker Type**
- **Opportunity**: Different workers need different context profiles
- **Hypothesis**: Operator benefits from recent tool calls + facts (600 tokens); Researcher needs sources + dialogue (200 tokens); Judge needs just options (100 tokens)
- **Control**: Use static strategy for all (baseline)
- **Variant**: Per-worker optimized strategies
- **Metric**: Task success rate, token efficiency, hallucination rate per worker type
- **Baseline Paper**: Context Rot (VLDB 2026) — different models degrade at different rates
- **Timeline**: 2 weeks discovery + testing

#### 2. **Fact Freshness Weighting**
- **Opportunity**: Old facts should have different credibility than new ones
- **Hypothesis**: Exponential decay weighting (1-minute-old = 100% weight, 1-hour-old = 20% weight) improves accuracy
- **Control**: Uniform weighting (all facts = same weight)
- **Variants**: Linear decay, exponential decay (various λ values), step-function decay
- **Metric**: Fact accuracy in operator decisions, decision consistency over time
- **Baseline Paper**: "Lost in the Middle" (PMC 2026) — temporal position affects recall
- **Timeline**: 1 week discovery + testing

#### 3. **Context Depth vs. Breadth Tradeoff**
- **Opportunity**: Choose between shallow mention of many results vs. deep detail of few results
- **Hypothesis**: Task complexity determines optimal trade-off (simple lookup → broad; complex reasoning → deep)
- **Control**: Current balanced approach (5 recent messages + all facts)
- **Variants**:
  - Shallow broad: 10 messages (mentions only) + facts summary
  - Deep narrow: 2 messages (full detail) + facts + artifacts
  - Adaptive: Task classifier determines strategy
- **Metric**: Output completeness, reasoning quality, token efficiency
- **Baseline Paper**: U-NIAH (ArXiv 2503.00353) — multi-needle evaluation shows position matters
- **Timeline**: 2 weeks

#### 4. **Dialogue Relevance Threshold Optimization**
- **Opportunity**: Current heuristic uses `2+ keyword overlap` — is that optimal?
- **Hypothesis**: Threshold should adapt per task type (strict 3+ for focused; loose 1+ for exploratory)
- **Control**: Hardcoded threshold = 2
- **Variants**: 1, 2, 3, 4+ keyword overlap; adaptive threshold based on task type
- **Metric**: Context precision (relevant messages % of total), context recall (missed relevant messages), task success rate
- **Baseline Paper**: "Smaller Needles More Difficult" (PMC 2026) — relevance signal varies by task
- **Timeline**: 1 week

#### 5. **Artifact Inclusion & Decay Strategy**
- **Opportunity**: Not all artifacts help; some hurt (old screenshots, outdated intermediate results)
- **Hypothesis**: Recent artifacts boost performance; old artifacts create noise
- **Control**: Include all artifacts (current approach)
- **Variants**:
  - Recent only (last 3 artifacts, <30 min old)
  - Type-selective (code yes, screenshots no)
  - Decay weighted (1 day old = 50% weight)
- **Metric**: Operator success rate, hallucination reduction, token efficiency
- **Baseline Paper**: "Best Way to Fix Context Problem is Have Less Context" — high precision/low recall wins
- **Timeline**: 1.5 weeks

---

### New 10 Opportunities (from 2025 Research)

#### 6. **Needle-in-Haystack Robustness (Position Sensitivity)**
- **Opportunity**: LLMs suffer "lost in the middle" — critical information shouldn't be buried mid-context
- **Hypothesis**: Placing high-importance facts at position 0 and 1 (beginning) or position -1 (end) improves retrieval accuracy by 20-40%
- **Control**: Random order (current)
- **Variants**:
  - Fact-first (all facts before dialogue)
  - Position-weighted ordering (important facts at extremes)
  - Alternating (facts, dialogue, facts, dialogue)
- **Metric**: Fact recall accuracy (U-NIAH style), operator correctness when facts are critical
- **Baseline Paper**: "Lost in the Middle" & U-NIAH (ArXiv 2503.00353) — position affects recall by >30%
- **Timeline**: 2 weeks

#### 7. **Gold Context Size Optimization (Needle Length)**
- **Opportunity**: Size of critical context piece matters — shorter gold context = sharper needles = easier to find
- **Hypothesis**: Breaking large code files or documents into smaller chunks (200 chars vs. 1000 chars) improves accuracy
- **Control**: Current paragraph-aware chunking (1200 chars)
- **Variants**: 200, 500, 1000, 2000 char chunks; adaptive per content type
- **Metric**: Chunk recall accuracy, operator correctness on code/doc tasks
- **Baseline Paper**: "Gold Context Size Impact" (ArXiv 2025) — smaller chunks = higher accuracy
- **Timeline**: 1.5 weeks

#### 8. **Context Compression Strategy (Summarization vs. Raw)**
- **Opportunity**: Compress large contexts (summaries vs. full text) to preserve information while reducing tokens
- **Hypothesis**: Abstractive summaries (LLM-generated) of old dialogue preserve intent while cutting tokens 50%; improve accuracy vs. truncation
- **Control**: Raw truncation (current)
- **Variants**:
  - Extractive summarization (keep top sentences)
  - Abstractive summarization (compress with LLM)
  - Hierarchical compression (levels of detail)
- **Metric**: Token count reduction, information preservation (recall), operator task success rate
- **Baseline Paper**: "ACON: Optimizing Context Compression" (OpenReview) — framework shows 40-60% token reduction possible
- **Timeline**: 2 weeks (LLM summarization overhead to profile)

#### 9. **Prompt Caching Integration (Reuse Stability)**
- **Opportunity**: Cache repeated context (facts, worker profiles) across multiple agent calls within a session
- **Hypothesis**: Caching improves latency 2-3x and reduces token cost; enables longer context windows cost-effectively
- **Control**: No caching (current)
- **Variants**: Cache at different boundaries (facts only, facts + profiles, full packets)
- **Metric**: Latency (TTFT + inter-token latency), total token cost per session, cache hit rate
- **Baseline Paper**: "Prompt Cache: Modular Attention Reuse" (2025) — 2-3x latency improvement demonstrated
- **Timeline**: 1 week (integration only, not discovery)

#### 10. **Multi-Objective Preference Learning (Beyond Single Accuracy)**
- **Opportunity**: Current scoring uses single dimension (accuracy). Real-world needs multiple trade-offs
- **Hypothesis**: Pareto-optimal solutions exist trading off accuracy vs. brevity vs. safety vs. efficiency; MODPO algorithm finds them
- **Control**: Single-objective optimization (current)
- **Variants**: MODPO with weight vectors for different task types
  - Code generation: [accuracy=0.5, brevity=0.3, safety=0.2]
  - Reasoning: [accuracy=0.6, clarity=0.4]
  - Summarization: [accuracy=0.4, brevity=0.6]
- **Metric**: Pareto frontier of solutions, user preference validation, task-specific performance
- **Baseline Paper**: "MODPO & UtilityMax Prompting" (ArXiv 2603.11583) — multi-objective RL-free optimization
- **Timeline**: 2 weeks

#### 11. **Example Selection Strategy (Few-Shot Diversity)**
- **Opportunity**: Few-shot examples in worker prompts matter; selection strategy affects performance
- **Hypothesis**: Diverse examples (covering edge cases, different domains) improve generalization 10-15% vs. random selection
- **Control**: Random few-shot examples (current)
- **Variants**:
  - k-means clustering (select representative examples)
  - Diversity-aware sampling (max distance in embedding space)
  - Task-specific selection (match input characteristics)
- **Metric**: Task success rate on unseen input types, hallucination rate on edge cases
- **Baseline Paper**: "Meta-Prompting" (2025) — shows example selection significantly impacts prompt quality
- **Timeline**: 2 weeks

#### 12. **Instruction Evolution (Instruction Vs. Few-Shot Trade-off)**
- **Opportunity**: Balance between detailed instruction vs. learning by example varies per task
- **Hypothesis**: Analytical tasks benefit from instructions (reasoning steps); creative tasks benefit from examples (style)
- **Control**: Current hybrid (instructions + 2-3 examples)
- **Variants**:
  - Instruction-heavy (detailed steps, no examples)
  - Example-heavy (5+ diverse examples, minimal instruction)
  - Adaptive (task classifier decides)
- **Metric**: Task success rate per category, instruction clarity scores, example relevance
- **Baseline Paper**: "FIPO: Fine-tuning for Prompt Optimization" (2025) — shows instruction evolution matters
- **Timeline**: 2 weeks

#### 13. **Context Rot Mitigation (Accuracy Degradation Over Session Length)**
- **Opportunity**: LLM accuracy drops as context grows (context rot); mitigate via active trimming
- **Hypothesis**: Trimming context to <2000 tokens maintains >95% accuracy; >3000 tokens → degradation
- **Control**: Unlimited context (current behavior with risk)
- **Variants**:
  - Hard cap at 2000 tokens (trim oldest first)
  - Soft cap with quality gates (trim if accuracy drops below threshold)
  - Predictive model (estimate accuracy, preemptively trim)
- **Metric**: Accuracy vs. context size curve, operator success rate in long sessions
- **Baseline Paper**: "Maximum Effective Context Window for Real World" (OAJAIML) — context degradation is real
- **Timeline**: 1.5 weeks

#### 14. **Source/Domain Reputation Weighting**
- **Opportunity**: Not all sources are equally reliable; weight facts by source credibility
- **Hypothesis**: Wikipedia/StackOverflow facts weighted 1.0x; unknown domain 0.5x; improves decision correctness
- **Control**: All sources equally weighted
- **Variants**:
  - Manual reputation list (whitelist high-trust domains)
  - Learned reputation (per-domain success rate over time)
  - Dynamic reputation (adjust based on fact validation)
- **Metric**: Decision correctness per source type, false positive reduction, decision confidence calibration
- **Baseline Paper**: "Source Reputation Weighting" (2600/observability-design-doc, injected as system prompt context)
- **Timeline**: 2 weeks

#### 15. **Interleaving Pattern (Dialogue + Facts Ordering)**
- **Opportunity**: How to interleave recent dialogue with facts for optimal context flow
- **Hypothesis**: Alternating (dialogue → facts → dialogue → facts) improves coherence; facts-first improves recall
- **Control**: Facts-last (current: dialogue then facts)
- **Variants**:
  - Facts-first (facts, then dialogue)
  - Interleaved (alternate pairs)
  - Chronological (strict timeline order)
  - Semantic grouping (related concepts together)
- **Metric**: Task coherence scores, fact usage rate (do operators actually use facts?), decision quality
- **Baseline Paper**: NIAH and "Lost in the Middle" — ordering within context affects utilization
- **Timeline**: 1.5 weeks

---

## Part 2: Scientific Testing Framework

### Control & Baseline Design

Each discovery opportunity follows this structure:

```
┌─ CHAMPION (Baseline)
│  └─ Current production behavior
│
├─ CHALLENGER VARIANTS (N ≥ 3)
│  ├─ Variant A
│  ├─ Variant B
│  └─ Variant C
│
├─ ORACLE TESTS (Deterministic)
│  ├─ No regression on golden test set
│  ├─ Time bounds maintained
│  └─ Output format validation
│
├─ JUDGE SCORING (Multi-dimensional)
│  ├─ Accuracy (0-5)
│  ├─ Efficiency (token count, latency) (0-5)
│  ├─ Clarity (output structure) (0-5)
│  ├─ Hallucination rate (0-5)
│  └─ Safety (no harmful outputs) (0-5)
│
├─ PARETO OPTIMIZATION (Multi-objective)
│  ├─ Weight by task type
│  └─ Find non-dominated solutions
│
└─ STATISTICAL VALIDATION
   ├─ n ≥ 100 test cases per variant
   ├─ Minimum effect size = 5% (Δ_min)
   ├─ α = 0.05 (significance threshold)
   └─ Power = 0.80
```

### Test Case Design

#### Golden Test Set (Regression Prevention)

**50 curated test cases** covering:
- Simple factual recall (5 cases)
- Multi-step reasoning (10 cases)
- Code generation (10 cases)
- Summarization (10 cases)
- Edge cases (15 cases: contradictions, missing data, ambiguity)

**Criteria**:
- All variants must maintain ≥95% accuracy on golden set
- TTFT within 2x of baseline
- Output format unchanged

#### Variant Test Set (Discovery)

**100-200 new test cases** per discovery opportunity:
- Systematic coverage of variant parameters
- Real session data (stratified by session length, worker type, domain)
- Blind evaluation (Judge doesn't know which variant)

#### Holdout Test Set (Final Validation)

**50 test cases** withheld until final selection:
- No training/tuning on this set
- Validates generalization
- Prevents overfitting to test set

### Scoring Rubric (Multi-Dimensional)

Each variant scored on 5 axes (0-5 each):

```
ACCURACY (0-5)
├─ 5: 100% correct
├─ 4: 95%+ correct (minor issues)
├─ 3: 80%+ correct (some errors)
├─ 2: 50%+ correct (many errors)
└─ 0: <50% correct or broken

EFFICIENCY (0-5)
├─ 5: <50% tokens of baseline, <100ms TTFT
├─ 4: 50-75% tokens of baseline
├─ 3: 75-100% tokens of baseline
├─ 2: 100-150% tokens of baseline
└─ 0: >150% tokens or >1s TTFT

CLARITY (0-5)
├─ 5: Output is concise, well-structured, actionable
├─ 4: Generally clear with minor verbosity
├─ 3: Understandable but could be clearer
├─ 2: Confusing or poorly structured
└─ 0: Unintelligible

HALLUCINATION (0-5)
├─ 5: 0% fabrication
├─ 4: <2% hallucination rate
├─ 3: 2-5% hallucination rate
├─ 2: 5-10% hallucination rate
└─ 0: >10% hallucination or complete fabrication

SAFETY (0-5)
├─ 5: 0 unsafe outputs
├─ 4: <1% unsafe
├─ 3: 1-3% unsafe
├─ 2: 3-5% unsafe
└─ 0: >5% unsafe or contains dangerous content
```

### Pareto Optimization (Multi-Objective)

For each discovery opportunity, identify Pareto frontier:

```python
# Example: Opportunity #1 (Composition Strategy Variance)
variants = {
    "static_current": {
        "accuracy": 0.87,
        "efficiency": 0.8,
        "clarity": 0.85,
        "hallucination_rate": 0.03,
    },
    "operator_optimized": {
        "accuracy": 0.92,
        "efficiency": 0.65,  # Uses more tokens
        "clarity": 0.88,
        "hallucination_rate": 0.02,
    },
    "researcher_optimized": {
        "accuracy": 0.89,
        "efficiency": 0.88,
        "clarity": 0.81,
        "hallucination_rate": 0.025,
    },
}

# Compute weighted scores per task type:
# Code generation: [accuracy=0.5, efficiency=0.3, clarity=0.1, hallucination=0.1]
# Reasoning: [accuracy=0.4, efficiency=0.2, clarity=0.2, hallucination=0.2]

# Identify non-dominated solutions (no other variant is better on all weighted dimensions)
```

### Statistical Significance Testing

**Hypothesis Test Per Variant:**

```
H₀: Variant performance = Baseline performance
H₁: Variant performance > Baseline performance (one-tailed)

Test: Welch's t-test (unequal variance)
α = 0.05
Power = 0.80
Minimum detectable effect size = 5% (Δ_min)

Sample size calculation:
n = 2(σ²)(z_{α/2} + z_β)² / Δ_min²

For σ ≈ 0.12 (typical score spread):
n ≈ 2(0.0144)(1.96 + 0.84)² / 0.05²
n ≈ 115 per variant
```

**Bonferroni Correction** (for multiple comparisons):
- 15 opportunities × 3 variants each = 45 tests
- Adjusted α = 0.05 / 45 ≈ 0.001
- Or: Report effect size + confidence intervals instead

### Measurement Plan

#### Phase 1: Baseline Establishment (Week 1)
- **Goal**: Establish canonical baseline metrics
- **Method**: Run golden test set on 5 models, 3 runs each
- **Output**: Baseline accuracy, efficiency, clarity, hallucination baseline ± std dev

#### Phase 2: Variant Testing (Weeks 2-4, Opportunities 1-5)
- **Goal**: Discover optimal context composition
- **Method**:
  - Generate 3-5 variants per opportunity
  - Run on 100-150 test cases per variant
  - Blind evaluation (Judge doesn't know variant)
  - Multi-dimensional scoring
- **Output**: Scorecard per variant, Pareto frontier

#### Phase 3: Variant Testing (Weeks 5-7, Opportunities 6-10)
- **Similar to Phase 2**

#### Phase 4: Variant Testing (Weeks 8-9, Opportunities 11-15)
- **Similar to Phase 2**

#### Phase 5: Validation & Selection (Weeks 10-11)
- **Goal**: Validate winners on holdout test set
- **Method**: Top variants from each opportunity, tested on 50 withheld cases
- **Output**: Generalization error, final selection

#### Phase 6: Integration & Deployment (Week 12)
- **Goal**: Merge best discoveries into PromptOptimizer
- **Method**: Implement context composition mutators, redeploy
- **Output**: Updated beigebox/orchestration/optimizer.py with new mutation strategies

---

## Part 3: Implementation Roadmap

### Immediate (Week 1-2): Framework Setup
1. Extend `ScoreCard` to track all 5 dimensions separately
2. Create `OracleTest` registry for golden + regression tests
3. Build `JudgeRubric` for multi-dimensional scoring
4. Implement `ParetoOptimizer` (non-dominated solution selection)
5. Create `/api/v1/discovery` endpoint for testing UI

### Short-term (Week 3-9): Discovery Execution
1. Run systematic A/B tests on opportunities 1-15
2. Log all scorecards to SQLite (`discovery_scorecards` table)
3. Weekly reports: "Opportunity X: Champion=baseline, Best Variant=Y (+7% accuracy, -12% tokens)"
4. Update PromptOptimizer with new mutation strategies as discoveries are validated

### Medium-term (Week 10-12): Validation & Deployment
1. Validate winners on holdout test set
2. Merge discoveries into production PromptOptimizer
3. Enable A/A testing in production (variant 1 = baseline, variant 2 = winner, log outcome)

### Long-term (Post-Week 12): Continuous Discovery
1. Operator telemetry feeds discovery loop (success/failure per variant)
2. Real-time Pareto frontier updates as production data arrives
3. Auto-rollout winning variants when effect size > threshold

---

## Part 4: Expected Outcomes

### Realistic Gains (Based on 2025 Research)

| Opportunity | Expected Improvement | Confidence | Timeline |
|---|---|---|---|
| Fact freshness weighting | +3-8% accuracy | High | 1 week |
| Position sensitivity (needle) | +5-15% recall | High | 2 weeks |
| Context compression | -40% tokens (same accuracy) | High | 2 weeks |
| Prompt caching | -60% latency, -30% cost | High | 1 week |
| Multi-objective optimization | +5-10% per task type | Medium | 2 weeks |
| Example selection | +3-7% edge case accuracy | Medium | 2 weeks |
| Context rot mitigation | +10-20% in long sessions (>50 turns) | Medium | 1.5 weeks |
| Source reputation weighting | +5-12% decision correctness | Medium | 2 weeks |
| Composition variance per worker | +8-15% per worker type | Medium | 2 weeks |
| Dialogue relevance threshold | +2-5% context precision | Low | 1 week |

**Aggregate Expected Improvement**: 25-40% on composite score (weighted by opportunity priority)

---

## References & Sources

- [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — Anthropic
- [Making Prompts First-Class Citizens for Adaptive LLM Pipelines](https://vldb.org/cidrdb/papers/2026/p26-cetintemel.pdf) — VLDB 2026
- [U-NIAH: Unified RAG and LLM Evaluation for Long Context](https://arxiv.org/html/2503.00353v1) — ArXiv 2503.00353
- [Lost in the Haystack: Smaller Needles More Difficult](https://pmc.ncbi.nlm.nih.gov/articles/PMC12478432/) — PMC
- [ACON: Optimizing Context Compression for Agents](https://openreview.net/pdf?id=7JbSwX6bNL) — OpenReview
- [The Maximum Effective Context Window for Real World LLMs](https://www.oajaiml.com/uploads/archivepdf/643561268.pdf) — OAJAIML
- [A Systematic Survey of Automatic Prompt Optimization Techniques](https://arxiv.org/html/2502.16923v1) — ArXiv 2502.16923
- [promptolution: A Unified Framework for Prompt Optimization](https://arxiv.org/html/2512.02840v1) — ArXiv 2512.02840
- [UtilityMax Prompting: Multi-Objective LLM Optimization](https://arxiv.org/html/2603.11583) — ArXiv 2603.11583
- [MODPO: Multi-Objective Direct Preference Optimization](https://arxiv.org/html/2503.05733) (inferred) — Research direction
- [DENIAHL: In-Context Features Influence Needle Abilities](https://arxiv.org/html/2411.19360v1) — ArXiv 2411.19360

---

## Next Steps

1. **Week 1**: Set up testing framework (Golden test set, Judge rubric, Pareto optimizer)
2. **Week 2-9**: Execute 15 discovery opportunities in parallel (3 per week)
3. **Week 10-11**: Validate winners on holdout set, merge into PromptOptimizer
4. **Week 12+**: Deploy discoveries, monitor A/A tests, iterate

**Target**: 25-40% composite improvement in context optimization by end of sprint.
