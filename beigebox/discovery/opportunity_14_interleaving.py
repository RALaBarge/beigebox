"""
Opportunity #14: Interleaving Pattern (Dialogue + Facts Ordering)

Hypothesis: How facts and dialogue turns are interleaved affects retrieval.
Facts-first ordering outperforms chronological for structured recall;
semantic grouping outperforms both for multi-topic reasoning.

Expected impact: +3-10% (MEDIUM confidence)

Variants
--------
- baseline_chronological:  Facts in arrival order (current)
- facts_first:             All injected facts before dialogue turns
- dialogue_first:          All dialogue turns before injected facts
- semantic_grouped:        Group turns by topic cluster (facts with related dialogue)
"""
from __future__ import annotations

import re
from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

_FACTS = [
    "Performance: P95 latency is 320ms. Target <200ms.",
    "Alice asked: Why is latency high? Bob answered: Cache miss storms.",
    "Feature: Semantic cache hit rate is 34%.",
    "Alice asked: What is the cache TTL? Bob answered: 300 seconds.",
    "Team: On-call rotation is 1 week per engineer, 4 engineers total.",
    "Alice asked: Who is on-call this week? Bob answered: Carol.",
    "Performance: Memory peaks at 4.2GB during batch jobs.",
    "Alice asked: Is 4.2GB safe? Bob answered: Yes, limit is 8GB.",
]

# Simple topic classifier: performance | feature | team
_TOPIC_PATTERNS = {
    "performance": ["latency", "memory", "P95", "4.2GB", "cache miss"],
    "feature":     ["cache", "TTL", "hit rate", "semantic"],
    "team":        ["on-call", "engineer", "Carol", "Alice", "Bob"],
}


def _classify_topic(content: str) -> str:
    for topic, keywords in _TOPIC_PATTERNS.items():
        if any(kw.lower() in content.lower() for kw in keywords):
            return topic
    return "other"


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


def _is_fact_turn(msg: dict) -> bool:
    return "asked:" not in msg.get("content", "") and "answered:" not in msg.get("content", "")


class InterleavingExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "interleaving_pattern"
    OPPORTUNITY_NAME = "Interleaving Pattern / Dialogue + Facts Ordering (#14)"
    HYPOTHESIS = "Facts-first and semantic grouping improve multi-topic recall vs. chronological"
    EXPECTED_IMPACT = "+3-10%"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_chronological", "interleave": "chrono"},
        {"name": "facts_first",            "interleave": "facts_first"},
        {"name": "dialogue_first",         "interleave": "dialogue_first"},
        {"name": "semantic_grouped",       "interleave": "semantic"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("interleave", "chrono")
        system, context, question = _get_body_turns(messages)

        if strategy == "chrono" or not context:
            return messages

        facts = [m for m in context if _is_fact_turn(m)]
        dialogue = [m for m in context if not _is_fact_turn(m)]

        if strategy == "facts_first":
            return system + facts + dialogue + [question]

        if strategy == "dialogue_first":
            return system + dialogue + facts + [question]

        if strategy == "semantic":
            # Group by topic: all performance turns, then feature, then team
            groups: dict[str, list[dict]] = {"performance": [], "feature": [], "team": [], "other": []}
            for msg in context:
                topic = _classify_topic(msg.get("content", ""))
                groups[topic].append(msg)
            ordered = (
                groups["performance"] + groups["feature"] + groups["team"] + groups["other"]
            )
            return system + ordered + [question]

        return messages

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the current P95 latency?",
                expected="320ms",
                task_type="performance_fact",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Why is latency high?",
                expected="cache miss",
                task_type="performance_dialogue",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the cache hit rate?",
                expected="34%",
                task_type="feature_fact",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the cache TTL?",
                expected="300",
                task_type="feature_dialogue",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Who is on-call this week?",
                expected="Carol",
                task_type="team_dialogue",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Is memory usage within safe limits?",
                expected="yes",
                task_type="performance_dialogue",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Summarize the performance metrics and team status.",
                expected="320ms",
                task_type="cross_topic_synthesis",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="List all facts about caching.",
                expected="34%",
                task_type="topic_recall",
            ),
        ]
