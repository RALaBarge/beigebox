"""
Opportunity #2: Fact Freshness Weighting

Hypothesis: Facts decay in relevance over time. Exponential decay weighting
(1-min-old = 100%, 1-hour-old = 20%) improves fact credibility and accuracy.

Expected impact: +3-8% accuracy (MEDIUM confidence)

Implementation: Each fact is tagged with a relative age in minutes.
Variants filter or reorder by age.

Variants
--------
- baseline_fifo:     Facts in arrival order (current)
- fresh_first:       Most recent facts moved to front
- decay_filter:      Discard facts older than 30 minutes
- decay_boost:       Keep all, but annotate staleness for the LLM
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

# Facts tagged with age in minutes (embedded in the fact text)
_FACTS = [
    "[age=60min] System status: All services operational. (from 1 hour ago)",
    "[age=45min] Deployment completed: version 2.3.1 is live.",
    "[age=30min] Alert resolved: high latency on /api/search endpoint.",
    "[age=15min] Cache hit rate dropped to 28% (was 34%).",
    "[age=5min] New deployment: hotfix 2.3.2 rolled out.",
    "[age=2min] Cache hit rate recovering: now 31%.",
    "[age=0min] Current P95 latency: 245ms.",
]


def _extract_age(content: str) -> int:
    """Parse [age=Nmin] tag from fact content. Returns 999 if not found."""
    import re
    match = re.search(r'\[age=(\d+)min\]', content)
    return int(match.group(1)) if match else 999


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class FactFreshnessExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "fact_freshness"
    OPPORTUNITY_NAME = "Fact Freshness Weighting (#2)"
    HYPOTHESIS = "Recency-weighted context improves accuracy for time-sensitive questions"
    EXPECTED_IMPACT = "+3-8% accuracy on time-sensitive queries"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_fifo",    "freshness_strategy": "fifo"},
        {"name": "fresh_first",      "freshness_strategy": "fresh_first"},
        {"name": "decay_filter",     "freshness_strategy": "decay_filter", "max_age_min": 30},
        {"name": "decay_boost",      "freshness_strategy": "decay_boost"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("freshness_strategy", "fifo")
        system, context, question = _get_body_turns(messages)

        if strategy == "fifo" or not context:
            return messages

        if strategy == "fresh_first":
            # Sort by age tag ascending (freshest first)
            sorted_ctx = sorted(context, key=lambda m: _extract_age(m.get("content", "")))
            return system + sorted_ctx + [question]

        if strategy == "decay_filter":
            max_age = variant_config.get("max_age_min", 30)
            fresh = [m for m in context if _extract_age(m.get("content", "")) <= max_age]
            # Always keep at least 2 turns for context
            if len(fresh) < 2:
                fresh = context[-2:]
            return system + fresh + [question]

        if strategy == "decay_boost":
            # Annotate stale facts with [STALE] marker so LLM can de-prioritize them
            annotated = []
            for msg in context:
                age = _extract_age(msg.get("content", ""))
                content = msg["content"]
                if age >= 30:
                    content = f"[STALE — {age}min old] {content}"
                annotated.append({**msg, "content": content})
            return system + annotated + [question]

        return messages

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the current P95 latency?",
                expected="245ms",
                task_type="fresh_fact_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the current cache hit rate?",
                expected="31%",
                task_type="fresh_fact_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What version is currently deployed?",
                expected="2.3.2",
                task_type="fresh_state_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What was the cache hit rate before the recent changes?",
                expected="34%",
                task_type="historical_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Describe the sequence of events in the past hour.",
                expected="deployment",
                task_type="chronological_synthesis",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Is there a current active alert?",
                expected="no",
                task_type="status_check",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What was the system status an hour ago?",
                expected="operational",
                task_type="stale_fact_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the trend in cache hit rate?",
                expected="recovering",
                task_type="trend_analysis",
            ),
        ]
