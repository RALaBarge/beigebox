"""
Opportunity #8: Context Depth vs. Breadth Strategy

Hypothesis: Task type determines optimal context distribution — simple lookups
benefit from broad recent context; complex reasoning benefits from deep
coverage of fewer topics.

Expected impact: +5-12% accuracy (MEDIUM confidence)

Variants
--------
- baseline_balanced:   Equal mix of topics and depth (current)
- broad_recent:        Keep only most recent 2 turns per topic cluster
- deep_focused:        Keep all turns for the topic most relevant to the question
- adaptive:            Select strategy based on question keywords (simple/complex)
"""
from __future__ import annotations

import re
from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

# Multi-topic context: 3 topic clusters, 4 facts each
_FACTS = [
    # Topic A: Performance
    "P95 latency is 320ms. Target is <200ms.",
    "Memory usage peaks at 4.2GB during batch jobs.",
    "CPU utilization averages 45%. Spikes to 90% on cache miss storms.",
    "We had 3 incidents last month: 2 latency spikes, 1 OOM crash.",
    # Topic B: Features
    "Feature flags are controlled via runtime_config.yaml.",
    "The semantic cache hit rate is 34%.",
    "Operator agent supports 12 tools: search, calculator, CDP, etc.",
    "MCP server exposes resident tools + discover_tools meta-tool.",
    # Topic C: Team
    "Alice owns the proxy pipeline. Bob owns storage.",
    "Releases happen every Friday at 5pm UTC.",
    "All PRs require review from the relevant owner.",
    "On-call rotation: 1 week per engineer, 4 engineers in rotation.",
]

_SIMPLE_KEYWORDS = {"what", "who", "when", "where", "which", "list", "how many"}
_COMPLEX_KEYWORDS = {"analyze", "compare", "trade-off", "synthesize", "explain why", "recommend"}


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


def _is_simple_query(question: str) -> bool:
    q_lower = question.lower()
    return any(kw in q_lower for kw in _SIMPLE_KEYWORDS) and not any(kw in q_lower for kw in _COMPLEX_KEYWORDS)


def _topic_relevance(question: str, turns: list[dict]) -> list[tuple[float, dict]]:
    """Score each turn by keyword overlap with question."""
    q_words = set(re.findall(r"\w+", question.lower()))
    scored = []
    for turn in turns:
        words = set(re.findall(r"\w+", turn.get("content", "").lower()))
        overlap = len(q_words & words)
        scored.append((overlap, turn))
    return scored


class DepthVsBreadthExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "context_depth_vs_breadth"
    OPPORTUNITY_NAME = "Context Depth vs. Breadth Strategy (#8)"
    HYPOTHESIS = "Task-adaptive depth/breadth selection improves accuracy vs. fixed mix"
    EXPECTED_IMPACT = "+5-12% accuracy"
    WEIGHT_PROFILE = "reasoning"

    VARIANTS = [
        {"name": "baseline_balanced", "strategy": "balanced"},
        {"name": "broad_recent",      "strategy": "broad",    "recent_n": 2},
        {"name": "deep_focused",      "strategy": "deep",     "top_k": 6},
        {"name": "adaptive",          "strategy": "adaptive"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("strategy", "balanced")
        system, context, question = _get_body_turns(messages)

        if strategy == "balanced" or not context:
            return messages

        if strategy == "broad":
            # Keep only the most recent N turns (breadth: sample across time)
            recent_n = variant_config.get("recent_n", 2) * 2  # n pairs = n*2 messages
            return system + context[-recent_n:] + [question]

        if strategy == "deep":
            # Keep the turns most semantically relevant to the question
            top_k = variant_config.get("top_k", 6)
            scored = _topic_relevance(question.get("content", ""), context)
            scored.sort(key=lambda x: x[0], reverse=True)
            kept = [turn for _, turn in scored[:top_k]]
            return system + kept + [question]

        if strategy == "adaptive":
            q_text = question.get("content", "")
            if _is_simple_query(q_text):
                # Simple query → broad: recent context
                recent_n = 4
                return system + context[-recent_n:] + [question]
            else:
                # Complex query → deep: most relevant turns
                top_k = min(8, len(context))
                scored = _topic_relevance(q_text, context)
                scored.sort(key=lambda x: x[0], reverse=True)
                kept = [turn for _, turn in scored[:top_k]]
                return system + kept + [question]

        return messages

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            # Simple lookups — breadth strategies should win
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the P95 latency target?",
                expected="200ms",
                task_type="simple_lookup",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Who owns the storage system?",
                expected="Bob",
                task_type="simple_lookup",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="When do releases happen?",
                expected="Friday",
                task_type="simple_lookup",
            ),
            # Complex reasoning — depth strategies should win
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Analyze the performance bottlenecks and recommend what to address first.",
                expected="latency",
                task_type="complex_reasoning",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Compare the observability we have for performance vs. team processes.",
                expected="metric",
                task_type="complex_comparison",
            ),
            # Mixed
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What tools does the operator agent support?",
                expected="12",
                task_type="simple_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Given the incident history, how resilient is the system?",
                expected="incident",
                task_type="complex_synthesis",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the semantic cache hit rate?",
                expected="34%",
                task_type="simple_lookup",
            ),
        ]
