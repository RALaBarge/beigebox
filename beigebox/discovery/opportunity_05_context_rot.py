"""
Opportunity #5: Context Rot Mitigation (#13 in original list)

Hypothesis: LLM accuracy degrades as context grows. Soft-capping at <2000 tokens
with quality gates maintains >95% accuracy in long sessions (>50 turns).

Expected impact: +10-20% accuracy in long sessions (HIGH confidence)

Research: VLDB 2026 — models fail with as few as 100 tokens in middle,
severe degradation by 1000 tokens. "Best fix: have less context."

Variants
--------
- baseline_unlimited:   Full context, no truncation (current)
- hard_cap_2000:        Hard truncate to last 2000 tokens (drop oldest)
- soft_cap_quality:     Keep newest turns until 2000 token budget, then summarise rest
- progressive_decay:    Full recent turns, summarize mid-range, drop oldest
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

# Simulate a 30-turn conversation — long enough to trigger context rot
_FACTS = [
    "Turn 1: User asked about the project scope. Scope: API gateway rewrite.",
    "Turn 2: Team size confirmed: 4 engineers. Alice leads.",
    "Turn 3: Budget: $120k for Q1 2026. Deadline March 31.",
    "Turn 4: Decision: Use FastAPI over Flask for async support.",
    "Turn 5: PostgreSQL chosen over MySQL for JSONB support.",
    "Turn 6: Redis cache with 300s default TTL configured.",
    "Turn 7: First deployment: version 1.0.0. No issues.",
    "Turn 8: Performance baseline: P95 = 850ms.",
    "Turn 9: Optimization round 1: added connection pooling. P95 → 420ms.",
    "Turn 10: Semantic cache implemented. Hit rate: 18%.",
    "Turn 11: Hit rate grew to 28% after 1 week.",
    "Turn 12: Decision: Add WASM transform modules.",
    "Turn 13: WASM module: opener_strip. Strips sycophantic openers.",
    "Turn 14: WASM module: pii_redactor. Redacts PII from responses.",
    "Turn 15: P95 now 380ms. Cache hit rate: 32%.",
    "Turn 16: New feature: CDP browser automation enabled.",
    "Turn 17: MCP server added. Exposes 9 resident tools.",
    "Turn 18: Operator agent: background execution implemented.",
    "Turn 19: Bug: bare except clause in ensemble_voter.py — fixed.",
    "Turn 20: Config refactor Phase 1 complete: features centralized.",
    "Turn 21: Auto-ingest system for staging docs deployed.",
    "Turn 22: 1,165 design docs indexed into ChromaDB.",
    "Turn 23: Current P95 latency: 320ms. Cache hit rate: 34%.",
    "Turn 24: Decision: Skip config Phase 4 and 5.",
    "Turn 25: MCP progressive tool disclosure implemented.",
    "Turn 26: discover_tools meta-tool: returns top-5 matching tools.",
    "Turn 27: Operator model UI field made editable.",
    "Turn 28: 74 design docs marked with status headers.",
    "Turn 29: Webhook egress: all 9 tests passing.",
    "LATEST: Current status — P95=320ms, cache=34%, 9 WASM modules, 1165 docs indexed.",
]

_APPROX_TOKENS_PER_FACT = 25  # rough estimate


def _approx_tokens(messages: list[dict]) -> int:
    return sum(len(m.get("content", "")) // 4 for m in messages)


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class ContextRotExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "context_rot"
    OPPORTUNITY_NAME = "Context Rot Mitigation (#5 / #13)"
    HYPOTHESIS = "Soft-capping context at <2000 tokens prevents accuracy degradation in long sessions"
    EXPECTED_IMPACT = "+10-20% accuracy in long sessions (>20 turns)"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_unlimited",   "rot_strategy": "none"},
        {"name": "hard_cap_recent",      "rot_strategy": "hard_cap",   "max_turns": 10},
        {"name": "soft_cap_quality",     "rot_strategy": "soft_cap",   "token_budget": 2000},
        {"name": "progressive_decay",    "rot_strategy": "decay"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("rot_strategy", "none")
        system, context, question = _get_body_turns(messages)

        if strategy == "none" or not context:
            return messages

        if strategy == "hard_cap":
            max_turns = variant_config.get("max_turns", 10)
            # Keep only the last max_turns × 2 messages (user+assistant pairs)
            return system + context[-(max_turns * 2):] + [question]

        if strategy == "soft_cap":
            budget = variant_config.get("token_budget", 2000)
            # Fill from newest backwards until budget exhausted
            selected = []
            used = _approx_tokens([question])
            for msg in reversed(context):
                cost = _approx_tokens([msg])
                if used + cost <= budget:
                    selected.insert(0, msg)
                    used += cost
                else:
                    break
            return system + selected + [question]

        if strategy == "decay":
            n = len(context)
            if n <= 8:
                return messages
            # Recent 8: full; middle 8: keep every other; old rest: drop
            recent = context[-8:]
            mid = context[max(0, n-16):-8]
            mid_sampled = mid[::2]  # every other
            return system + mid_sampled + recent + [question]

        return messages

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            # Recent facts — all strategies should handle these
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the current P95 latency?",
                expected="320ms",
                task_type="recent_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the current cache hit rate?",
                expected="34%",
                task_type="recent_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="How many docs are indexed?",
                expected="1,165",
                task_type="recent_recall",
            ),
            # Middle-age facts — hard_cap may miss these
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What does the pii_redactor WASM module do?",
                expected="PII",
                task_type="mid_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What was the first P95 baseline?",
                expected="850ms",
                task_type="early_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="When did the semantic cache hit rate first appear?",
                expected="18%",
                task_type="early_recall",
            ),
            # Old facts — only unlimited baseline should reliably recall these
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What framework was chosen over Flask?",
                expected="FastAPI",
                task_type="old_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What database was chosen and why?",
                expected="PostgreSQL",
                task_type="old_recall",
            ),
        ]
